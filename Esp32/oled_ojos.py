# oled_ojos.py — Expresiones RICAS para Creeper en OLED SH1106 128x64.
#
# Arquitectura:
#   - Cada estado emocional es un DICT de parámetros animables:
#       rx, ry          → tamaño del ojo
#       eyelid_top      → 0..1 cuánto baja el párpado superior
#       pupil_r         → radio de la pupila (0 = sin pupila, p.ej. FELIZ)
#       pupil_dx, dy    → desplazamiento de la pupila
#       brow_y          → offset vertical de la ceja
#       brow_angle      → -1 (frunce) .. 0 (recta) .. 1 (arqueada hacia arriba)
#       special         → modo especial ('half_bot' = solo mitad inferior ^^)
#
#   - `tick(estado, ...)` interpola el config actual hacia el target del
#     nuevo estado en `_morph_total` frames, agrega microsacadas y
#     sleepy progresivo en idle largo, y dibuja UN frame.
#
#   - `do_blink()` ejecuta una animación de parpadeo (~150 ms, 7 frames).
#
# Filosofía: máxima vivacidad. Cada llamada a tick() es un disp.show()
# completo (~25 ms de I2C). El caller decide la cadencia.

import math
import time
import random
from machine import Pin, SoftI2C
from sh1106 import SH1106_I2C


# ── Display ────────────────────────────────────────────────────
_i2c = SoftI2C(scl=Pin(22), sda=Pin(21), freq=400000)
disp = SH1106_I2C(128, 64, _i2c, Pin(16), addr=0x3C)


# ── Geometría ──────────────────────────────────────────────────
OJO_IZQ_X = 32
OJO_DER_X = 96
OJO_Y     = 32
MOUTH_CX  = 64
MOUTH_CY  = 55
PUPILA_MAX_DESP = 6


# ── Configuraciones por estado ─────────────────────────────────
# Notar los detalles:
#   ESCUCHANDO: ojos más abiertos + cejas levemente arqueadas (atento)
#   PENSANDO:   un ojo entrecerrado por arriba + pupila arriba-derecha
#               + cejas fruncidas (focalizado)
#   FELIZ:      solo mitad inferior (^^) + cejas onduladas
#   CURIOSO:    ojos más grandes + cejas muy arqueadas
#   HABLANDO:   ojos normales (la boca anima por separado)
_STATES = {
    'ESPERANDO':  {'rx':14, 'ry':11, 'pupil_r':4, 'pupil_dx':0, 'pupil_dy':0,
                   'eyelid_top':0.0, 'brow_y':-19, 'brow_angle':0.0,
                   'special':None},
    'ESCUCHANDO': {'rx':15, 'ry':13, 'pupil_r':4, 'pupil_dx':0, 'pupil_dy':0,
                   'eyelid_top':0.0, 'brow_y':-22, 'brow_angle':0.4,
                   'special':None},
    'PENSANDO':   {'rx':14, 'ry':11, 'pupil_r':3, 'pupil_dx':4, 'pupil_dy':-3,
                   'eyelid_top':0.35,'brow_y':-17, 'brow_angle':-0.6,
                   'special':None},
    'HABLANDO':   {'rx':14, 'ry':11, 'pupil_r':4, 'pupil_dx':0, 'pupil_dy':0,
                   'eyelid_top':0.0, 'brow_y':-19, 'brow_angle':0.2,
                   'special':None},
    'FELIZ':      {'rx':15, 'ry':10, 'pupil_r':0, 'pupil_dx':0, 'pupil_dy':0,
                   'eyelid_top':0.0, 'brow_y':-19, 'brow_angle':0.5,
                   'special':'half_bot'},
    'CURIOSO':    {'rx':17, 'ry':14, 'pupil_r':5, 'pupil_dx':0, 'pupil_dy':0,
                   'eyelid_top':0.0, 'brow_y':-23, 'brow_angle':0.8,
                   'special':None},
    'SIGUIENDO':  {'rx':14, 'ry':11, 'pupil_r':4, 'pupil_dx':0, 'pupil_dy':0,
                   'eyelid_top':0.0, 'brow_y':-19, 'brow_angle':0.1,
                   'special':None},
}

# Buffers de estado interno (sin allocs en runtime)
_current      = dict(_STATES['ESPERANDO'])
_target       = dict(_STATES['ESPERANDO'])
_morph_total  = 5
_morph_left   = 0
_last_state   = 'ESPERANDO'

# Microsacadas
_micro_dx     = 0
_micro_dy     = 0
_micro_tick   = 0
_MICRO_PERIOD = 25     # frames entre nuevos targets (~2.5 s @ 10 fps)


# ── Helpers geométricos ────────────────────────────────────────

def _fill_ellipse(cx, cy, rx, ry, color):
    """Elipse rellena scanline-por-scanline."""
    rx = int(rx)
    ry = int(ry)
    if rx <= 0 or ry <= 0:
        return
    ry2 = ry * ry
    rx2 = rx * rx
    for dy in range(-ry, ry + 1):
        val = rx2 * (ry2 - dy * dy)
        if val < 0:
            continue
        x = int(math.sqrt(val) / ry)
        disp.hline(cx - x, cy + dy, 2 * x + 1, color)

def _fill_ellipse_inferior(cx, cy, rx, ry, color):
    """Solo la mitad inferior (para ojos felices ^^)."""
    rx = int(rx)
    ry = int(ry)
    if rx <= 0 or ry <= 0:
        return
    ry2 = ry * ry
    rx2 = rx * rx
    for dy in range(0, ry + 1):
        val = rx2 * (ry2 - dy * dy)
        if val < 0:
            continue
        x = int(math.sqrt(val) / ry)
        disp.hline(cx - x, cy + dy, 2 * x + 1, color)


# ── Lerp de configs (para transiciones suaves) ─────────────────
def _lerp_to(t):
    """Interpola _current → _target con factor t, devuelve dict en RAM.
    Bools (special) se cambian al cruzar el 50%."""
    out = {}
    for k, av in _current.items():
        bv = _target[k]
        if isinstance(av, bool) or isinstance(av, str) or av is None or isinstance(bv, str) or bv is None:
            out[k] = bv if t >= 0.5 else av
        else:
            out[k] = av + (bv - av) * t
    return out


# ── Primitivas de dibujo ───────────────────────────────────────

def _draw_eye(cx, cy, c, mdx=0, mdy=0):
    """Dibuja UN ojo según el dict c. mdx/mdy son microsacadas."""
    if c.get('special') == 'half_bot':
        _fill_ellipse_inferior(cx, cy - 2, c['rx'], c['ry'] * 2, 1)
        return

    rx = max(1, int(c['rx']))
    ry = max(1, int(c['ry']))

    # Globo blanco
    _fill_ellipse(cx, cy, rx, ry, 1)

    # Párpado superior (rectángulo negro que cubre la parte de arriba)
    elt = c['eyelid_top']
    if elt > 0.02:
        h_clip = int(ry * 2 * elt)
        if h_clip > 0:
            disp.fill_rect(cx - rx - 1, cy - ry - 1, 2 * rx + 3, h_clip, 0)

    # Pupila (no dibujar si el párpado la cubre o si pupil_r == 0)
    pr = max(0, int(c['pupil_r']))
    if pr > 0 and elt < 0.85:
        px = cx + int(c['pupil_dx']) + mdx
        py = cy + int(c['pupil_dy']) + mdy
        # Mantener pupila dentro del ojo
        if px < cx - rx + pr: px = cx - rx + pr
        if px > cx + rx - pr: px = cx + rx - pr
        if py < cy - ry + pr: py = cy - ry + pr
        if py > cy + ry - pr: py = cy + ry - pr
        _fill_ellipse(px, py, pr, pr, 0)
        # Brillo de 2 px en la esquina sup-izq de la pupila
        disp.pixel(px - 1, py - 1, 1)
        disp.pixel(px - 2, py - 1, 1)

def _draw_brow(cx, cy_base, brow_y, angle):
    """Ceja como línea de 2 px de grosor. cx = centro X del ojo.
    angle: -1 frunce ( /\\ ), 0 recta, 1 arqueada ( \\/ ).
    Para el ojo IZQ el extremo externo es a la IZQUIERDA del cx.
    Para el DER, a la derecha."""
    y_center = cy_base + int(brow_y)
    if y_center < 1:
        return
    half_w = 12
    dy = int(angle * 4)
    if cx < 64:    # ojo izquierdo: outer = lado izq
        x1, y1 = cx - half_w, y_center - dy
        x2, y2 = cx + half_w, y_center + dy
    else:          # ojo derecho: outer = lado der
        x1, y1 = cx - half_w, y_center + dy
        x2, y2 = cx + half_w, y_center - dy
    disp.line(x1, y1, x2, y2, 1)
    disp.line(x1, y1 + 1, x2, y2 + 1, 1)

def _draw_mouth(open_amount):
    """Boca centrada en MOUTH_CX, MOUTH_CY. open_amount in [0, 1]."""
    if open_amount < 0.05:
        disp.line(MOUTH_CX - 5, MOUTH_CY, MOUTH_CX + 5, MOUTH_CY, 1)
    else:
        h = int(1 + open_amount * 4)
        _fill_ellipse(MOUTH_CX, MOUTH_CY, 6, h, 1)
        if h >= 3:
            _fill_ellipse(MOUTH_CX, MOUTH_CY, 4, h - 2, 0)

def _draw_zzz():
    """Tres Z's pequeñas en la esquina sup-derecha para sleepy."""
    disp.text('z', 92, 6, 1)
    disp.text('Z', 103, 1, 1)


# ── API PÚBLICA ────────────────────────────────────────────────

def reset_state():
    """Resetea el estado interno (útil al reconectarse)."""
    global _current, _target, _morph_left, _last_state
    global _micro_dx, _micro_dy, _micro_tick
    _current = dict(_STATES['ESPERANDO'])
    _target  = dict(_STATES['ESPERANDO'])
    _morph_left = 0
    _last_state = 'ESPERANDO'
    _micro_dx = 0
    _micro_dy = 0
    _micro_tick = 0


def tick(estado='ESPERANDO', sig_dx=0.0, sig_dy=0.0,
         idle_ms=0, mouth_amp=0.0):
    """
    Renderiza UN frame con todos los adornos:
      - Transición lerp si el estado cambió (5 frames)
      - Microsacadas en estados 'alerta' (ESPERANDO, ESCUCHANDO, PENSANDO)
      - Sleepy progresivo en ESPERANDO si idle_ms > 15 s
      - Pupila siguiendo en SIGUIENDO
      - Boca animada en HABLANDO según mouth_amp [0, 1]
    Devuelve True si dibujó, False si saltó por estado desconocido.
    """
    global _current, _target, _morph_left, _last_state
    global _micro_dx, _micro_dy, _micro_tick

    # Cambio de estado → iniciar morph
    if estado != _last_state:
        if estado in _STATES:
            _target = dict(_STATES[estado])
        else:
            _target = dict(_STATES['ESPERANDO'])
        _morph_left = _morph_total
        _last_state = estado

    # Aplicar paso de morph
    if _morph_left > 0:
        t = 1.0 - (_morph_left - 1) / _morph_total
        rendering = _lerp_to(t)
        _morph_left -= 1
        if _morph_left == 0:
            # Snap final
            for k in _current:
                _current[k] = _target[k]
    else:
        rendering = dict(_current)

    # Microsacadas (solo en estados conscientes, no SIGUIENDO ni FELIZ)
    if estado in ('ESPERANDO', 'ESCUCHANDO', 'PENSANDO'):
        _micro_tick += 1
        if _micro_tick >= _MICRO_PERIOD:
            _micro_tick = 0
            _micro_dx = random.randint(-2, 2)
            _micro_dy = random.randint(-1, 1)
    else:
        _micro_dx = 0
        _micro_dy = 0

    # SIGUIENDO: override de pupila desde rostro detectado
    if estado == 'SIGUIENDO':
        if sig_dx >  1.0: sig_dx =  1.0
        elif sig_dx < -1.0: sig_dx = -1.0
        if sig_dy >  1.0: sig_dy =  1.0
        elif sig_dy < -1.0: sig_dy = -1.0
        rendering['pupil_dx'] = sig_dx * PUPILA_MAX_DESP
        rendering['pupil_dy'] = sig_dy * PUPILA_MAX_DESP

    # SLEEPY: párpado baja progresivamente en ESPERANDO largo
    show_zzz = False
    if estado == 'ESPERANDO':
        if idle_ms > 15000:
            sleep_factor = (idle_ms - 15000) / 15000.0
            if sleep_factor > 1.0:
                sleep_factor = 1.0
            target_lid = sleep_factor * 0.65
            if target_lid > rendering['eyelid_top']:
                rendering['eyelid_top'] = target_lid
        if idle_ms > 30000:
            rendering['eyelid_top'] = 0.95     # casi cerrados
            if (idle_ms // 1500) & 1 == 0:
                show_zzz = True

    # DRAW
    disp.fill(0)
    _draw_eye(OJO_IZQ_X, OJO_Y, rendering, _micro_dx, _micro_dy)
    _draw_eye(OJO_DER_X, OJO_Y, rendering, _micro_dx, _micro_dy)

    # Cejas: solo si los ojos están razonablemente abiertos
    if rendering['eyelid_top'] < 0.8 and rendering.get('special') != 'half_bot':
        _draw_brow(OJO_IZQ_X, OJO_Y, rendering['brow_y'], rendering['brow_angle'])
        _draw_brow(OJO_DER_X, OJO_Y, rendering['brow_y'], rendering['brow_angle'])
    elif rendering.get('special') == 'half_bot':
        # Cejas extra arqueadas para FELIZ
        _draw_brow(OJO_IZQ_X, OJO_Y, rendering['brow_y'], rendering['brow_angle'])
        _draw_brow(OJO_DER_X, OJO_Y, rendering['brow_y'], rendering['brow_angle'])

    # Boca durante HABLANDO
    if estado == 'HABLANDO' or mouth_amp > 0:
        _draw_mouth(mouth_amp)

    if show_zzz:
        _draw_zzz()

    disp.show()
    return True


def do_blink():
    """Parpadeo: cierre + apertura suave en ~150 ms (7 frames)."""
    # Usar el config actual como base
    fases = (1.0, 0.4, 0.0, 0.0, 0.4, 0.8, 1.0)   # multiplicador de ry
    for f in fases:
        cfg = dict(_current)
        cfg['ry'] = max(1, int(_current['ry'] * f))
        cfg['eyelid_top'] = 0
        disp.fill(0)
        _draw_eye(OJO_IZQ_X, OJO_Y, cfg, _micro_dx, _micro_dy)
        _draw_eye(OJO_DER_X, OJO_Y, cfg, _micro_dx, _micro_dy)
        if f > 0.5 and cfg.get('special') != 'half_bot':
            _draw_brow(OJO_IZQ_X, OJO_Y, cfg['brow_y'], cfg['brow_angle'])
            _draw_brow(OJO_DER_X, OJO_Y, cfg['brow_y'], cfg['brow_angle'])
        disp.show()
        time.sleep_ms(20)


def do_wink(eye='left'):
    """Guiño asimétrico: cierra UN solo ojo brevemente."""
    fases = (1.0, 0.3, 0.0, 0.3, 1.0)
    for f in fases:
        cfg_open  = dict(_current)
        cfg_close = dict(_current)
        cfg_close['ry'] = max(1, int(_current['ry'] * f))
        cfg_close['eyelid_top'] = 0
        disp.fill(0)
        if eye == 'left':
            _draw_eye(OJO_IZQ_X, OJO_Y, cfg_close)
            _draw_eye(OJO_DER_X, OJO_Y, cfg_open)
        else:
            _draw_eye(OJO_IZQ_X, OJO_Y, cfg_open)
            _draw_eye(OJO_DER_X, OJO_Y, cfg_close)
        if cfg_open.get('special') != 'half_bot':
            _draw_brow(OJO_IZQ_X, OJO_Y, cfg_open['brow_y'], cfg_open['brow_angle'])
            _draw_brow(OJO_DER_X, OJO_Y, cfg_open['brow_y'], cfg_open['brow_angle'])
        disp.show()
        time.sleep_ms(35)


# ── API legacy (back-compat con código previo) ─────────────────

def ojos_normal():     tick('ESPERANDO')
def ojos_abiertos():   tick('ESCUCHANDO')
def ojos_pensando():   tick('PENSANDO')
def ojos_feliz():      tick('FELIZ')
def ojos_curioso():    tick('CURIOSO')
def ojos_siguiendo(dx, dy): tick('SIGUIENDO', sig_dx=dx, sig_dy=dy)
def parpadear():       do_blink()

def ojos_hablando(frame):
    # Convertimos el contador a una amplitud tipo seno
    amp = abs(math.sin(frame * 0.4)) * 0.85
    tick('HABLANDO', mouth_amp=amp)


# ══════════════════════════════════════════════════════════════
# DEMO
# ══════════════════════════════════════════════════════════════
if __name__ == '__main__':
    print('Demo oled_ojos (rich edition)')
    estados = ['ESPERANDO', 'ESCUCHANDO', 'PENSANDO',
               'HABLANDO', 'FELIZ', 'CURIOSO']
    try:
        while True:
            for s in estados:
                print(' ->', s)
                for i in range(30):
                    if s == 'HABLANDO':
                        amp = abs(math.sin(i * 0.4)) * 0.85
                        tick(s, mouth_amp=amp)
                    else:
                        tick(s, idle_ms=i * 100)
                    time.sleep_ms(100)
                if s == 'ESPERANDO':
                    print('   parpadeo')
                    do_blink()
                if s == 'FELIZ':
                    print('   guiño')
                    do_wink('left')
            # Probar SIGUIENDO
            print(' -> SIGUIENDO (círculo)')
            for ang in range(0, 360, 12):
                rad = ang * 0.01745
                tick('SIGUIENDO', sig_dx=math.cos(rad), sig_dy=math.sin(rad))
                time.sleep_ms(80)
            # Probar SLEEPY
            print(' -> Sleepy (40 s de ESPERANDO)')
            for i in range(400):
                tick('ESPERANDO', idle_ms=i * 100)
                time.sleep_ms(100)
    except KeyboardInterrupt:
        reset_state()
        tick('ESPERANDO')
        print('demo terminada')

