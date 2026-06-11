# oled_ojos_prove.py — Sistema de Ojos Emocionales para Bob (Creeper)
# Diseño: "Squircles pulidos" con glint humano + comportamiento autónomo.
# Pantalla OLED SH1106 128x64 monocromática sobre MicroPython/ESP32.
#
# Fases implementadas:
#   1. Base de ojos (squircles + cejas)            — APROBADO
#   2. 20 emociones                                — APROBADO
#   3. Microexpresiones (blink, sacadas, doble)    — APROBADO
#   4. Personalidad: iniciativa, guiño, quirks     — APROBADO
#   5. Sistema de habla SIN BOCA — ojos cargan      — APROBADO
#      pulsación, bounces, smile-eyes, brow lifts.
#   6. Sistema de atención: acquisition + search    — APROBADO
#   7. Aburrimiento escalonado en 'neutral'          — ESTE ARCHIVO
#      (0/10/30/60/120/300 s → normal, mirar,
#       pensativo, aburrido, somnoliento, dormido).
#   8. Transición de sueño adorable: blink lento +   — ESTE ARCHIVO
#      cierre gradual de ojos → 'durmiendo' con Zzz.
#   9. Showcase: tour guiado de todas las fases.     — ESTE ARCHIVO
#
# Arquitectura:
#   EyeState  — estado independiente por ojo (habilita guiño asimétrico)
#   Behavior  — estado compartido (sacadas, iniciativa, quirks, sig_dx/dy)
#   update_*  — actualizadores por tick
#   render()  — dibuja la cara según estado actual + todos los modificadores
#   tick()    — API compatible con Esp32/oled_ojos.py (UPPERCASE estados)
#               para que este archivo pueda reemplazar la firmware actual.

from machine import Pin, I2C
from sh1106_2 import SH1106
import time
import math
import random

# ============================================================
# HARDWARE
# ============================================================
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100000)
oled = SH1106(128, 64, i2c)

# ============================================================
# CONFIGURACIÓN GLOBAL
# ============================================================
ESTADOS = [
    'neutral', 'feliz', 'muy_feliz', 'escuchando', 'pensando',
    'curioso', 'sorprendido', 'confundido', 'triste', 'muy_triste',
    'enojado', 'sospechando', 'travieso', 'orgulloso', 'dormido',
    'durmiendo', 'procesando', 'error', 'siguiendo', 'amor', 'hablando'
]

# Mapeo desde nombres UPPERCASE del SerialManager (robot_bob/state_machine.py)
# a los nombres lowercase usados internamente. Para reemplazo transparente
# de Esp32/oled_ojos.py por este archivo.
PRODUCTION_STATES = {
    'ESPERANDO':  'neutral',      # IDLE / CONVERSATION_IDLE
    'ESCUCHANDO': 'escuchando',   # LISTENING
    'PENSANDO':   'pensando',     # THINKING
    'HABLANDO':   'hablando',     # SPEAKING (sin boca — ojos)
    'FELIZ':      'feliz',
    'CURIOSO':    'curioso',      # incluido para "doble toma" del behavior
    'SIGUIENDO':  'siguiendo',    # PRESENCE
}

# Estados donde el ojo PUEDE parpadear (excluye dormido, error, etc.)
# 'hablando' INCLUIDO — durante el habla parpadea más frecuente (ver update_blinks)
_BLINK_ALLOWED  = ('neutral', 'feliz', 'muy_feliz', 'escuchando', 'pensando',
                   'curioso', 'sorprendido', 'confundido', 'triste', 'muy_triste',
                   'enojado', 'sospechando', 'travieso', 'procesando', 'siguiendo',
                   'hablando')

# Estados donde la pupila puede moverse por sacadas
# 'hablando' INCLUIDO — engagement con el listener vía micro-sacadas
_SACCADE_ALLOWED = ('neutral', 'escuchando', 'pensando', 'curioso', 'sorprendido',
                    'sospechando', 'procesando', 'siguiendo', 'feliz', 'muy_feliz',
                    'travieso', 'orgulloso', 'hablando')

# Estados donde Bob puede ser "espontáneo" (mirada de iniciativa)
# 'hablando' NO incluido — Bob no se distrae cuando habla
_INITIATIVE_ALLOWED = ('neutral', 'escuchando', 'curioso', 'siguiendo')

# Estados donde Bob puede guiñar — 'hablando' NO (rompe el ritmo del discurso)
_WINK_ALLOWED = ('neutral', 'feliz', 'muy_feliz', 'travieso', 'curioso')

# Estados donde pueden ocurrir reacciones inesperadas (quirks)
# 'hablando' NO — un estornudo a mitad de frase queda raro
_QUIRK_ALLOWED = ('neutral', 'escuchando', 'pensando', 'curioso', 'feliz', 'travieso',
                  'siguiendo')

# ── FASE 6 — Sistema de atención ──────────────────────────────────────
# Acquisition: gesto al detectar cara (ojos se abren, pupila salta)
_ACQ_DURATION_MS    = 500      # duración total del gesto
_ACQ_PEAK_RX        = 3        # boost de rx al pico
_ACQ_PEAK_RY        = 3        # boost de ry al pico
# Search: pupila escanea cuando se pierde la cara
_SEARCH_DURATION_MS = 1800
_SEARCH_AMPLITUDE   = 7        # px máximo del escaneo lateral
# Smoothing exponencial del tracking (0..1, mayor = más suave/lento)
_TRACK_SMOOTH_ALPHA = 0.70

# ── FASE 7 — Sistema de aburrimiento ──────────────────────────────────
# Umbrales en ms de tiempo en 'neutral' sin interacción.
# Nivel: 0=normal, 1=mirar más, 2=pensativo, 3=aburrido, 4=somnoliento, 5=dormido.
_BOREDOM_THRESHOLDS_MS = (10_000, 30_000, 60_000, 120_000, 300_000)
# Encoge ry (ojos más chicos) según nivel — efecto visible "ojos cansados"
_BOREDOM_RY_SHRINK = (0, 0, 1, 2, 5, 8)
# Multiplicador de cadencia de blink (alto = blinks más espaciados)
_BOREDOM_BLINK_MULT = (1.0, 1.0, 1.2, 1.5, 1.8, 2.0)

# ── FASE 8 — Transición de sueño ──────────────────────────────────────
# Cuando boredom_level llega a 5, se gatilla una transición adorable
# antes de pasar al estado 'durmiendo' que muestra los Zzz.
_SLEEP_TRANSITION_MS = 3000     # duración del cierre gradual de ojos


# ============================================================
# TIMING HELPER
# ============================================================
def _get_t():
    """Milisegundos compatibles MicroPython / PC."""
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)


# ============================================================
# ESTADO POR OJO (habilita guiño asimétrico)
# ============================================================
class EyeState:
    __slots__ = ('name', 'blink_active', 'blink_start_t', 'blink_is_double',
                 'is_wink')

    def __init__(self, name):
        self.name = name
        self.blink_active   = False
        self.blink_start_t  = 0
        self.blink_is_double = False
        self.is_wink        = False    # True si este blink es un guiño asimétrico

    def start_blink(self, t, is_double=False, is_wink=False):
        self.blink_active   = True
        self.blink_start_t  = t
        self.blink_is_double = is_double
        self.is_wink        = is_wink

    def blink_factor(self, t):
        """Devuelve 0..1 (1=cerrado). Maneja doble parpadeo automáticamente."""
        if not self.blink_active:
            return 0.0
        dt = t - self.blink_start_t
        if dt < 60:
            return dt / 60.0
        elif dt < 100:
            return 1.0
        elif dt < 160:
            return 1.0 - (dt - 100) / 60.0
        else:
            self.blink_active = False
            if self.blink_is_double:
                self.blink_is_double = False
                self.blink_active = True
                self.blink_start_t = t
            return 0.0


# ============================================================
# ESTADO DE COMPORTAMIENTO COMPARTIDO
# ============================================================
class Behavior:
    """Estado global de las microexpresiones, iniciativa y quirks."""

    def __init__(self):
        # Estado emocional actual
        self.estado = 'neutral'

        # Blinks coordinados (ambos ojos sincronizados)
        self.next_blink_t = random.randint(1500, 3500)

        # Sacadas (ambos ojos miran al mismo punto)
        self.saccade_dx     = 0
        self.saccade_dy     = 0
        self.saccade_active = False
        self.saccade_end_t  = 0
        self.next_saccade_t = random.randint(900, 2500)

        # Mirada de iniciativa (look-around macro espontáneo)
        self.initiative_dx       = 0
        self.initiative_dy       = 0
        self.initiative_active   = False
        self.initiative_end_t    = 0
        self.next_initiative_t   = random.randint(8000, 16000)

        # Guiño asimétrico
        self.next_wink_t = random.randint(15000, 35000)

        # Reacciones inesperadas (quirks)
        self.quirk_type      = None       # 'startle' | 'wander' | 'sneeze' | 'double_take'
        self.quirk_start_t   = 0
        self.quirk_duration  = 0
        self.next_quirk_t    = random.randint(45000, 120000)

        # Offset normalizado [-1..1] que el tracker envía durante 'siguiendo'.
        # Se actualiza desde set_following_offset(dx, dy).
        self.sig_dx = 0.0
        self.sig_dy = 0.0

        # Fase de jitter para el envelope de 'hablando' (evita patrón perceptible)
        self.speech_jitter_a = random.uniform(0, 6.28)
        self.speech_jitter_b = random.uniform(0, 6.28)

        # ── FASE 6: Sistema de atención ────────────────────────
        # Tracking suavizado (pupila durante 'siguiendo')
        self.track_dx_smooth = 0.0
        self.track_dy_smooth = 0.0
        # Acquisition gesture (cara nueva detectada)
        self.acq_active    = False
        self.acq_start_t   = 0
        # Search gesture (cara perdida → escaneo)
        self.search_active  = False
        self.search_start_t = 0
        self.search_dx      = 0
        self.search_dy      = 0
        # Memoria del estado anterior para detectar transiciones
        self.prev_estado = 'neutral'

        # ── FASE 7: Sistema de aburrimiento ────────────────────
        self.last_interaction_t = _get_t() if False else 0  # se setea al primer render
        self.boredom_level      = 0
        self._boredom_override  = None  # debug: None | 0..5

        # ── FASE 8: Transición a sueño ─────────────────────────
        self.sleep_transition_active = False
        self.sleep_transition_start_t = 0


# Instancias singleton (módulo)
eye_l = EyeState('L')
eye_r = EyeState('R')
beh   = Behavior()


# ============================================================
# UPDATERS DE MICRO-EXPRESIONES
# ============================================================
def update_blinks(t):
    """
    Parpadeo coordinado L+R. Solo dispara si ambos ojos están libres
    (sin guiño activo).
    """
    if beh.estado not in _BLINK_ALLOWED:
        return
    if eye_l.blink_active or eye_r.blink_active:
        return
    if t < beh.next_blink_t:
        return
    is_double = random.random() < 0.15
    eye_l.start_blink(t, is_double=is_double, is_wink=False)
    eye_r.start_blink(t, is_double=is_double, is_wink=False)
    # Cadencia adaptativa: hablando parpadea más seguido (más vivo)
    if beh.estado == 'hablando':
        base = random.randint(1400, 3200)
    else:
        base = random.randint(2200, 6500)
    # FASE 7: aburrimiento alarga el intervalo de blink (parpadeos pesados)
    bored_mult = _BOREDOM_BLINK_MULT[min(5, beh.boredom_level)]
    beh.next_blink_t = t + int(base * bored_mult)


def update_wink(t):
    """Guiño asimétrico ocasional: solo un ojo cierra brevemente."""
    if beh.estado not in _WINK_ALLOWED:
        return
    if t < beh.next_wink_t:
        return
    # Elegir ojo random y disparar guiño solo si está libre
    target = eye_l if random.random() < 0.5 else eye_r
    if not target.blink_active:
        target.start_blink(t, is_double=False, is_wink=True)
    beh.next_wink_t = t + random.randint(18000, 42000)


def update_saccade(t):
    """Microsacadas (jitter sutil) + miradas espontáneas (desplazamiento medio)."""
    if beh.estado not in _SACCADE_ALLOWED:
        beh.saccade_dx = 0
        beh.saccade_dy = 0
        beh.saccade_active = False
        return

    if t >= beh.next_saccade_t:
        r = random.random()
        if r < 0.25:
            # Mirada espontánea (desplazamiento medio-largo)
            beh.saccade_dx = random.choice([-5, -3, 3, 5])
            beh.saccade_dy = random.choice([-2, -1, 1, 2])
            duration = random.randint(500, 1200)
            beh.next_saccade_t = t + duration + random.randint(1500, 4000)
            beh.saccade_end_t  = t + duration
            beh.saccade_active = True
        elif r < 0.75:
            # Microsacada rápida (jitter sutil)
            beh.saccade_dx = random.choice([-1, 0, 1])
            beh.saccade_dy = random.choice([-1, 0, 1])
            duration = random.randint(80, 200)
            beh.next_saccade_t = t + duration + random.randint(400, 1500)
            beh.saccade_end_t  = t + duration
            beh.saccade_active = True
        else:
            beh.next_saccade_t = t + random.randint(800, 2500)

    if beh.saccade_active and t >= beh.saccade_end_t:
        beh.saccade_dx = 0
        beh.saccade_dy = 0
        beh.saccade_active = False


def update_initiative(t):
    """
    Mirada de iniciativa: cada 8-16 s Bob mira espontáneamente a una dirección
    "como si algo le llamara la atención", luego vuelve. Solo en estados pasivos.
    """
    if beh.estado not in _INITIATIVE_ALLOWED:
        if beh.initiative_active:
            beh.initiative_active = False
            beh.initiative_dx = 0
            beh.initiative_dy = 0
        return

    if beh.initiative_active:
        if t >= beh.initiative_end_t:
            beh.initiative_active = False
            beh.initiative_dx = 0
            beh.initiative_dy = 0
            beh.next_initiative_t = t + random.randint(8000, 16000)
        return

    if t < beh.next_initiative_t:
        return

    # Disparar: mira a un cuadrante random con desplazamiento mayor que sacada normal
    beh.initiative_dx = random.choice([-8, -6, 6, 8])
    beh.initiative_dy = random.choice([-4, -3, 3, 4])
    beh.initiative_end_t  = t + random.randint(600, 1400)
    beh.initiative_active = True


def update_boredom(t):
    """
    FASE 7+8: Sistema de aburrimiento → sueño.

    Tiempo en 'neutral' sin interacción escala el nivel (0-5):
      0  [0-10s]    normal
      1  [10-30s]   mirar más (sacadas más amplias — heredado de iniciativa)
      2  [30-60s]   pensativo (cejas levantadas, ojos un poco más chicos)
      3  [60-120s]  aburrido (ojos visiblemente más chicos, blinks lentos)
      4  [120-300s] somnoliento (ojos a la mitad, blinks muy lentos)
      5  [300+s]    arranca transición a 'durmiendo' (Fase 8)

    Cualquier transición de estado (otra cosa que neutral) resetea el timer.
    """
    estado = beh.estado

    # Override de debug (force_boredom_level) — ignora timing real
    if beh._boredom_override is not None:
        beh.boredom_level = beh._boredom_override
        if beh.boredom_level >= 5 and not beh.sleep_transition_active:
            beh.sleep_transition_active = True
            beh.sleep_transition_start_t = t
        return

    # Si no estamos en 'neutral', resetear todo (Bob está interactuando)
    if estado != 'neutral':
        beh.last_interaction_t = t
        beh.boredom_level = 0
        beh.sleep_transition_active = False
        return

    # Inicializar last_interaction_t en el primer frame
    if beh.last_interaction_t == 0:
        beh.last_interaction_t = t
        return

    idle_ms = t - beh.last_interaction_t
    # Mapear a nivel según umbrales
    level = 0
    for thr in _BOREDOM_THRESHOLDS_MS:
        if idle_ms >= thr:
            level += 1
        else:
            break
    beh.boredom_level = level

    # Nivel 5: gatillar transición a sueño si no está ya activa
    if level >= 5 and not beh.sleep_transition_active:
        beh.sleep_transition_active = True
        beh.sleep_transition_start_t = t

    # Si la transición está activa, chequear si ya terminó
    if beh.sleep_transition_active:
        elapsed = t - beh.sleep_transition_start_t
        if elapsed >= _SLEEP_TRANSITION_MS:
            # Cambiar a 'durmiendo' (que dibuja Zzz)
            beh.estado = 'durmiendo'
            beh.sleep_transition_active = False


def update_attention(t):
    """
    FASE 6: Sistema de atención.
      - Detecta transición → 'siguiendo' → dispara acquisition gesture
      - Suaviza tracking durante 'siguiendo' (smoothing exponencial)
      - Detecta 'siguiendo' → 'neutral' → dispara search gesture
      - Mantiene prev_estado para detectar futuras transiciones
    """
    estado = beh.estado
    prev   = beh.prev_estado

    # ── Transición A: cara recién detectada ───────────────────────
    if estado == 'siguiendo' and prev != 'siguiendo':
        beh.acq_active  = True
        beh.acq_start_t = t
        # Snap inmediato del smoothing al target real (sin ramp inicial)
        beh.track_dx_smooth = beh.sig_dx * 8.0
        beh.track_dy_smooth = beh.sig_dy * 6.0
        # Si había search activo (caso raro de re-detección rápida), cancelar
        beh.search_active = False
        beh.search_dx     = 0
        beh.search_dy     = 0

    # ── Transición B: cara perdida hacia estado pasivo ────────────
    # Solo si vamos a 'neutral'. Si vamos a hablando/escuchando, Bob está
    # engaged con la conversación y no debe "buscar".
    if prev == 'siguiendo' and estado == 'neutral':
        beh.search_active  = True
        beh.search_start_t = t

    # ── Smoothing del tracking durante 'siguiendo' ────────────────
    if estado == 'siguiendo':
        target_dx = beh.sig_dx * 8.0
        target_dy = beh.sig_dy * 6.0
        a = _TRACK_SMOOTH_ALPHA
        beh.track_dx_smooth = a * beh.track_dx_smooth + (1.0 - a) * target_dx
        beh.track_dy_smooth = a * beh.track_dy_smooth + (1.0 - a) * target_dy

    # ── Timeout del acquisition ───────────────────────────────────
    if beh.acq_active and (t - beh.acq_start_t) >= _ACQ_DURATION_MS:
        beh.acq_active = False

    # ── Animación del search ──────────────────────────────────────
    if beh.search_active:
        dt = t - beh.search_start_t
        if dt >= _SEARCH_DURATION_MS:
            beh.search_active = False
            beh.search_dx = 0
            beh.search_dy = 0
        else:
            # Oscilación seno con amplitud decreciente: 1 ciclo derecha→izq
            phase = dt / _SEARCH_DURATION_MS    # 0..1
            decay = 1.0 - phase * 0.4           # de 1.0 a 0.6
            beh.search_dx = int(_SEARCH_AMPLITUDE * decay *
                                math.sin(2.0 * math.pi * phase))
            beh.search_dy = 0

    beh.prev_estado = estado


def update_quirk(t):
    """
    Reacciones inesperadas. Cada 45-120s, en estados ALLOWED, Bob hace algo
    imprevisto. Tipos:
      - startle:     ojos grandes + shake (susto leve)
      - wander:      mirada perdida hacia arriba
      - sneeze:      ojos cerrados fuerte + shake (estornudo visual)
      - double_take: mira a un lado, después al otro (chequeo doble)
    """
    if beh.estado not in _QUIRK_ALLOWED:
        if beh.quirk_type is not None:
            beh.quirk_type = None
        return

    if beh.quirk_type is not None:
        if t >= beh.quirk_start_t + beh.quirk_duration:
            beh.quirk_type = None
            beh.next_quirk_t = t + random.randint(45000, 120000)
        return

    if t < beh.next_quirk_t:
        return

    beh.quirk_type = random.choice(('startle', 'wander', 'sneeze', 'double_take'))
    beh.quirk_start_t = t
    beh.quirk_duration = {
        'startle':     500,
        'wander':      1600,
        'sneeze':      350,
        'double_take': 900,
    }[beh.quirk_type]


# ============================================================
# CALCULAR MODIFICADORES DE QUIRK PARA EL FRAME ACTUAL
# ============================================================
def _quirk_modifiers(t):
    """
    Devuelve overrides para el frame actual según el quirk activo.
    Llaves: dx_pupil, dy_pupil, drx, dry, force_blink (0..1), shake_x, shake_y
    """
    out = {'dx_pupil': 0, 'dy_pupil': 0, 'drx': 0, 'dry': 0,
           'force_blink': 0.0, 'shake_x': 0, 'shake_y': 0}
    if beh.quirk_type is None:
        return out

    dt = t - beh.quirk_start_t
    qd = beh.quirk_duration

    if beh.quirk_type == 'startle':
        # Primeros 150ms: ojos se expanden y vibran
        if dt < 150:
            out['drx'] = 3
            out['dry'] = 5
            out['shake_x'] = random.choice((-2, -1, 1, 2))
            out['shake_y'] = random.choice((-1, 0, 1))
        else:
            # Recuperación gradual
            phase = 1.0 - (dt - 150) / max(1, qd - 150)
            out['drx'] = int(3 * phase)
            out['dry'] = int(5 * phase)

    elif beh.quirk_type == 'wander':
        # Mirada perdida hacia arriba durante toda la duración
        out['dy_pupil'] = -8
        out['dx_pupil'] = int(math.sin(dt * 0.004) * 2)  # leve oscilación lateral

    elif beh.quirk_type == 'sneeze':
        # Cierra fuerte los primeros 200ms con shake
        if dt < 200:
            out['force_blink'] = 1.0
            out['shake_x'] = random.choice((-2, -1, 1, 2))
            out['shake_y'] = random.choice((-1, 1))
        else:
            # Abre con vibración menor
            out['shake_x'] = random.choice((-1, 0, 1))

    elif beh.quirk_type == 'double_take':
        # Mira a un lado, vuelve al centro, mira al otro
        seg = qd // 3
        if dt < seg:
            out['dx_pupil'] = 8
            out['dy_pupil'] = 0
        elif dt < 2 * seg:
            out['dx_pupil'] = 0
        else:
            out['dx_pupil'] = -8
            out['dy_pupil'] = 0

    return out


# ============================================================
# PRIMITIVAS GEOMÉTRICAS (idénticas a antes)
# ============================================================
def fill_circle(cx, cy, r, color=1):
    for dy in range(-r, r + 1):
        ny = cy + dy
        if ny < 0 or ny >= 64: continue
        dx = int(math.sqrt(max(0, r * r - dy * dy)))
        x1 = max(0, cx - dx)
        x2 = min(127, cx + dx)
        if x2 > x1: oled.hline(x1, ny, x2 - x1, color)


def fill_ellipse(cx, cy, rx, ry, color=1):
    if rx <= 0 or ry <= 0: return
    ry2 = ry * ry
    rx2 = rx * rx
    for dy in range(-ry, ry + 1):
        ny = cy + dy
        if ny < 0 or ny >= 64: continue
        val = rx2 * (ry2 - dy * dy)
        if val < 0: continue
        dx = int(math.sqrt(val) / ry) if ry else 0
        x1 = max(0, cx - dx)
        x2 = min(127, cx + dx)
        if x2 > x1: oled.hline(x1, ny, x2 - x1, color)


def fill_polygon(pts, color=1):
    n = len(pts)
    if n < 3: return
    miny = max(0, min(p[1] for p in pts))
    maxy = min(63, max(p[1] for p in pts))
    for y in range(miny, maxy + 1):
        xs = []
        j = n - 1
        for i in range(n):
            yi, yj = pts[i][1], pts[j][1]
            if (yi <= y < yj) or (yj <= y < yi):
                x = pts[i][0] + (y - yi) * (pts[j][0] - pts[i][0]) // (yj - yi) if (yj - yi) else pts[i][0]
                xs.append(x)
            j = i
        xs.sort()
        for k in range(0, len(xs), 2):
            x1 = max(0, xs[k])
            x2 = min(127, xs[k + 1])
            if x2 > x1: oled.hline(x1, y, x2 - x1, color)


def fill_superellipse(cx, cy, rx, ry, n=4, color=1):
    """Squircle (superelipse con n=4). Optimizado con sqrt dobles."""
    if rx <= 0 or ry <= 0: return
    for dy in range(-ry, ry + 1):
        ny = cy + dy
        if ny < 0 or ny >= 64: continue
        ratio = dy / ry
        val = 1.0 - ratio * ratio * ratio * ratio
        if val < 0: val = 0
        dx = int(rx * math.sqrt(math.sqrt(val)))
        x1 = max(0, cx - dx)
        x2 = min(127, cx + dx)
        if x2 >= x1:
            oled.hline(x1, ny, x2 - x1 + 1, color)


def fill_heart(cx, cy, r, color=1):
    fill_circle(cx - r // 2, cy - r // 3, r // 2, color)
    fill_circle(cx + r // 2, cy - r // 3, r // 2, color)
    pts = [(cx - r, cy - r // 3), (cx + r, cy - r // 3), (cx, cy + r)]
    fill_polygon(pts, color)


# ============================================================
# DIBUJO DE CEJAS
# ============================================================
def draw_brow(cx, cy_base, ry, angle, offset_y=-4):
    """Ceja recta inclinada con grosor 2px."""
    y_center = cy_base - ry + offset_y
    if y_center < 1: return
    half_w = 16
    dy = int(angle * 5)
    if cx < 64:
        x1, y1 = cx - half_w, y_center + dy
        x2, y2 = cx + half_w, y_center - dy
    else:
        x1, y1 = cx - half_w, y_center - dy
        x2, y2 = cx + half_w, y_center + dy
    oled.line(x1, y1, x2, y2, 1)
    oled.line(x1, y1 + 1, x2, y2 + 1, 1)


def draw_arch_brow(cx, cy_base, ry, offset_y=-4, peak_height=4):
    """Ceja arqueada (pico tipo feliz) con grosor 2px."""
    y_base = cy_base - ry + offset_y
    if y_base < peak_height + 1: return
    half_w = 16
    oled.line(cx - half_w, y_base, cx, y_base - peak_height, 1)
    oled.line(cx, y_base - peak_height, cx + half_w, y_base, 1)
    oled.line(cx - half_w, y_base + 1, cx, y_base - peak_height + 1, 1)
    oled.line(cx, y_base - peak_height + 1, cx + half_w, y_base + 1, 1)


# ============================================================
# PUPILA CON GLINT MEJORADO (visual polish)
# ============================================================
def draw_squircle_pupil(cx, rx, ry_current, px, py, prx=8, pry=6):
    """
    Dibuja pupila squircle con glint principal 2x2 + sub-glint 1px diagonal opuesto.
    Le da el efecto 'ojo húmedo / vivo'.
    """
    if ry_current < pry + 2:
        return

    # Clamp px/py al globo blanco
    if px < cx - rx + prx: px = cx - rx + prx
    if px > cx + rx - prx: px = cx + rx - prx
    # cy implícito está en el llamador via py
    fill_superellipse(px, py, prx, pry, n=4, color=0)

    # Glint principal 2x2 cuadrante superior-izquierdo
    gx = px - prx + 2
    gy = py - pry + 2
    oled.pixel(gx,     gy,     1)
    oled.pixel(gx + 1, gy,     1)
    oled.pixel(gx,     gy + 1, 1)
    oled.pixel(gx + 1, gy + 1, 1)

    # Sub-glint 1px en cuadrante inferior-derecho (efecto humedad)
    sx = px + prx - 3
    sy = py + pry - 3
    oled.pixel(sx, sy, 1)


# ============================================================
# DIBUJO DE OJO INDIVIDUAL (con per-eye state)
# ============================================================
def _draw_eye(cx, cy, rx, ry, side, eye_state, t, qm):
    """
    Dibuja un solo ojo aplicando estado por-ojo (parpadeo/guiño) + modificadores.
    qm: dict de quirk modifiers
    """
    estado = beh.estado

    # ── 1. Shake del quirk ────────────────────────────────────────────
    cx += qm['shake_x']
    cy += qm['shake_y']

    # ── 2. Boost de tamaño (startle) ──────────────────────────────────
    rx_eff = rx + qm['drx']
    ry_target = ry + qm['dry']

    # ── 3. Blink factor per-ojo (habilita guiños independientes) ──────
    can_blink = estado in _BLINK_ALLOWED
    lid_factor = eye_state.blink_factor(t) if can_blink else 0.0
    # Force blink por quirk (sneeze)
    if qm['force_blink'] > lid_factor:
        lid_factor = qm['force_blink']

    ry_current = int(ry_target * (1.0 - lid_factor)) if lid_factor > 0.0 else ry_target

    # ── 3b. FASE 7: encoger ojos por aburrimiento (solo en 'neutral') ──
    if estado == 'neutral' and beh.boredom_level > 0:
        shrink = _BOREDOM_RY_SHRINK[min(5, beh.boredom_level)]
        ry_current = max(2, ry_current - shrink)

    # ── 3c. FASE 8: cierre gradual durante sleep transition ───────────
    if beh.sleep_transition_active:
        elapsed = t - beh.sleep_transition_start_t
        phase = elapsed / _SLEEP_TRANSITION_MS
        if phase > 1.0: phase = 1.0
        # ry colapsa de 100% a 10% en la duración del transition
        ry_current = int(ry_current * (1.0 - phase * 0.90))

    # ── 4. Ojo cerrado → línea horizontal ─────────────────────────────
    if ry_current <= 2:
        oled.hline(cx - 15, cy + 3, 31, 1)
        oled.hline(cx - 15, cy + 4, 31, 1)
        draw_brow(cx, cy, 4, 0, offset_y=-5)
        return

    # ── 5. Pupila base con offset acumulado ───────────────────────────
    # Suma: sacadas + iniciativa + quirk + search (Fase 6)
    px_off = beh.saccade_dx + beh.initiative_dx + qm['dx_pupil'] + beh.search_dx
    py_off = beh.saccade_dy + beh.initiative_dy + qm['dy_pupil'] + beh.search_dy

    # ── 6. Render por estado emocional ────────────────────────────────
    if estado == 'neutral':
        # Vibración orgánica con jitter para evitar patrón perceptible
        jx = random.uniform(0.85, 1.15)
        jy = random.uniform(0.85, 1.15)
        dx = int(math.sin(t * 0.001 * jx) * 1.5)
        dy = int(math.cos(t * 0.0015 * jy) * 0.8)
        fill_superellipse(cx + dx, cy + dy, rx_eff, ry_current)
        draw_squircle_pupil(cx + dx, rx_eff, ry_current,
                            cx + dx + px_off, cy + dy + py_off)
        draw_brow(cx, cy, ry, 0)

    elif estado == 'feliz':
        # Bounce con leve perturbación + microsacada permitida en pupila simulada
        bounce_phase = t * 0.004 + random.uniform(-0.05, 0.05)
        dy_bounce = int(abs(math.sin(bounce_phase)) * 3)
        # Ojo arqueado (parte inferior del squircle)
        fill_superellipse(cx, cy - dy_bounce, rx_eff, ry_current)
        fill_ellipse(cx, cy - dy_bounce + 10, rx_eff + 4, ry_current - 3, 0)
        draw_arch_brow(cx, cy - dy_bounce, ry, offset_y=-5, peak_height=3)
        # Sparkle sutil al costado (efecto chispas de felicidad)
        if (t // 200) % 4 == 0:
            spx = cx + (-rx_eff - 5 if side == 'L' else rx_eff + 5)
            spy = cy - dy_bounce - 8
            if 0 <= spx < 128 and 0 <= spy < 64:
                oled.pixel(spx, spy, 1)
                oled.pixel(spx + 1, spy + 1, 1)
                oled.pixel(spx - 1, spy + 1, 1)
                oled.pixel(spx, spy + 2, 1)

    elif estado == 'muy_feliz':
        bounce_phase = t * 0.006 + random.uniform(-0.05, 0.05)
        dy_bounce = int(abs(math.sin(bounce_phase)) * 5)
        fill_superellipse(cx, cy - dy_bounce, rx_eff, ry_current - 2)
        fill_ellipse(cx, cy - dy_bounce + 6, rx_eff + 4, ry_current - 4, 0)
        by = cy - dy_bounce + ry_current + 2
        if by < 63:
            for bx in (cx - 14, cx - 9, cx + 9, cx + 14):
                oled.pixel(bx, by, 1)
                oled.pixel(bx + 1, by - 1, 1)
        draw_arch_brow(cx, cy - dy_bounce, ry - 2, offset_y=-7, peak_height=5)

    elif estado == 'escuchando':
        pulse = math.sin(t * 0.005) * 1.0
        fill_superellipse(cx, cy - 2, int(rx_eff + pulse), ry_current + 2)
        draw_squircle_pupil(cx, rx_eff, ry_current + 2,
                            cx + px_off, cy - 2 + py_off)
        draw_brow(cx, cy - 2, ry + 2, 0, offset_y=-7)

    elif estado == 'pensando':
        fill_superellipse(cx, cy, rx_eff, ry_current)
        # Pupila mira arriba-derecha (clásico gesto de pensar)
        ppx, ppy = 5, -4
        draw_squircle_pupil(cx, rx_eff, ry_current,
                            cx + ppx + px_off, cy + ppy + py_off, 7, 5)
        if side == 'L':
            oled.fill_rect(cx - rx_eff - 1, cy - ry_current - 1, 2*rx_eff + 3, ry_current // 2, 0)
            draw_brow(cx, cy, ry, -0.6)
        else:
            oled.fill_rect(cx - rx_eff - 1, cy - ry_current - 1, 2*rx_eff + 3, ry_current // 3, 0)
            draw_brow(cx, cy, ry, 0.4, offset_y=-8)

    elif estado == 'curioso':
        if side == 'L':
            h_wobble = int(math.sin(t * 0.003) * 2)
            fill_superellipse(cx, cy - 3, rx_eff, ry_current + 3 + h_wobble)
            draw_squircle_pupil(cx, rx_eff, ry_current + 3 + h_wobble,
                                cx + px_off, cy - 3 + py_off)
            draw_brow(cx, cy - 3, ry + 3 + h_wobble, 0.3, offset_y=-9)
        else:
            h_wobble = int(math.cos(t * 0.003) * 2)
            fill_superellipse(cx, cy + 2, rx_eff - 3, ry_current - 3 + h_wobble)
            draw_squircle_pupil(cx, rx_eff - 3, ry_current - 3 + h_wobble,
                                cx + px_off, cy + 2 + py_off, 6, 5)
            draw_brow(cx, cy + 2, ry - 3 + h_wobble, -0.4, offset_y=-3)

    elif estado == 'sorprendido':
        shake_x = random.randint(-1, 1) if t % 2 == 0 else 0
        shake_y = random.randint(-1, 1) if t % 3 == 0 else 0
        fill_superellipse(cx + shake_x, cy + shake_y - 2, rx_eff - 2, ry_current + 4)
        draw_squircle_pupil(cx + shake_x, rx_eff - 2, ry_current + 4,
                            cx + shake_x + px_off, cy + shake_y - 2 + py_off, 4, 3)
        draw_arch_brow(cx + shake_x, cy + shake_y - 2, ry + 4,
                       offset_y=-9, peak_height=4)

    elif estado == 'confundido':
        fill_superellipse(cx, cy, rx_eff - 2, ry_current - 2)
        if side == 'L':
            pts = [(cx - rx_eff, cy - ry_current), (cx + 2, cy - ry_current),
                   (cx - rx_eff, cy + 2)]
            fill_polygon(pts, 0)
            draw_brow(cx, cy, ry - 2, 0.5)
        else:
            pts_r = [(cx - rx_eff, cy - ry_current), (cx + 2, cy - ry_current),
                     (cx - rx_eff, cy + 3)]
            fill_polygon(pts_r, 0)
            draw_brow(cx, cy, ry - 2, -0.6)

    elif estado == 'triste':
        fill_superellipse(cx, cy, rx_eff, ry_current)
        if side == 'L':
            pts = [(cx - rx_eff - 2, cy - ry_current - 2), (cx, cy - ry_current - 2),
                   (cx - rx_eff - 2, cy)]
        else:
            pts = [(cx + rx_eff + 2, cy - ry_current - 2), (cx, cy - ry_current - 2),
                   (cx + rx_eff + 2, cy)]
        fill_polygon(pts, 0)
        draw_squircle_pupil(cx, rx_eff, ry_current, cx, cy + 3, 8, 5)
        draw_brow(cx, cy, ry, 0.7)
        if side == 'L':
            tear_dy = int((t * 0.015) % 22)
            tx, ty = cx - rx_eff + 3, cy + 3 + tear_dy
            if ty < 62:
                fill_circle(tx, ty, 2, 1)
                oled.pixel(tx, ty - 2, 1)

    elif estado == 'muy_triste':
        fill_superellipse(cx, cy - 1, rx_eff, ry_current - 2)
        if side == 'L':
            pts = [(cx - rx_eff - 2, cy - ry_current - 3), (cx + 3, cy - ry_current - 3),
                   (cx - rx_eff - 2, cy + 2)]
        else:
            pts = [(cx + rx_eff + 2, cy - ry_current - 3), (cx - 3, cy - ry_current - 3),
                   (cx + rx_eff + 2, cy + 2)]
        fill_polygon(pts, 0)
        ppx = 2 if side == 'L' else -2
        draw_squircle_pupil(cx, rx_eff, ry_current - 2, cx + ppx, cy + 3, 7, 5)
        draw_brow(cx, cy - 1, ry - 2, 0.9)
        tear_dy = int((t * 0.02) % 24)
        tx = cx - rx_eff + 5 if side == 'L' else cx + rx_eff - 5
        ty = cy + 2 + tear_dy
        if ty < 62:
            fill_circle(tx, ty, 2, 1)
            oled.pixel(tx, ty - 2, 1)

    elif estado == 'enojado':
        shake = random.randint(-1, 1) if t % 2 == 0 else 0
        fill_superellipse(cx + shake, cy, rx_eff, ry_current)
        if side == 'L':
            pts = [(cx + rx_eff + 2, cy - ry_current - 2), (cx - 3, cy - ry_current - 2),
                   (cx + rx_eff + 2, cy)]
        else:
            pts = [(cx - rx_eff - 2, cy - ry_current - 2), (cx + 3, cy - ry_current - 2),
                   (cx - rx_eff - 2, cy)]
        fill_polygon(pts, 0)
        draw_squircle_pupil(cx + shake, rx_eff, ry_current,
                            cx + shake, cy + 1)
        draw_brow(cx + shake, cy, ry, -0.8, offset_y=-2)

    elif estado == 'sospechando':
        look_x = int(math.sin(t * 0.002) * 6)
        fill_superellipse(cx, cy + 3, rx_eff, ry_current - 8)
        draw_squircle_pupil(cx, rx_eff, ry_current - 8,
                            cx + look_x + px_off, cy + 3 + py_off, 8, 5)
        draw_brow(cx, cy + 3, ry - 8, 0, offset_y=-2)

    elif estado == 'travieso':
        bounce_phase = t * 0.005 + random.uniform(-0.04, 0.04)
        dy_bounce = int(abs(math.sin(bounce_phase)) * 4)
        if side == 'L':
            fill_superellipse(cx, cy - dy_bounce, rx_eff, ry_current)
            pts = [(cx + rx_eff + 2, cy - dy_bounce - ry_current - 2),
                   (cx - 3, cy - dy_bounce - ry_current - 2),
                   (cx + rx_eff + 2, cy - dy_bounce + 1)]
            fill_polygon(pts, 0)
            # Pupila con microsacada (estado activo)
            draw_squircle_pupil(cx, rx_eff, ry_current,
                                cx + px_off, cy - dy_bounce + py_off)
            draw_brow(cx, cy - dy_bounce, ry, -0.6)
        else:
            bx = cx
            by = cy - dy_bounce + 2
            oled.line(bx - 14, by, bx - 7, by + 4, 1)
            oled.line(bx - 7, by + 4, bx + 7, by + 4, 1)
            oled.line(bx + 7, by + 4, bx + 14, by, 1)
            oled.line(bx - 14, by + 1, bx - 7, by + 5, 1)
            oled.line(bx - 7, by + 5, bx + 7, by + 5, 1)
            oled.line(bx + 7, by + 5, bx + 14, by + 1, 1)
            draw_arch_brow(cx, cy - dy_bounce, ry, offset_y=-5, peak_height=3)

    elif estado == 'orgulloso':
        # Respiración con jitter sutil
        breath_phase = t * 0.002 + random.uniform(-0.03, 0.03)
        dy_breath = int(math.sin(breath_phase) * 3.0)
        bx = cx
        by = cy - 3 + dy_breath
        oled.line(bx - 14, by + 4, bx - 7, by, 1)
        oled.line(bx - 7, by, bx + 7, by, 1)
        oled.line(bx + 7, by, bx + 14, by + 4, 1)
        oled.line(bx - 14, by + 5, bx - 7, by + 1, 1)
        oled.line(bx - 7, by + 1, bx + 7, by + 1, 1)
        oled.line(bx + 7, by + 1, bx + 14, by + 5, 1)
        draw_arch_brow(cx, cy - 3 + dy_breath, 10, offset_y=-8, peak_height=4)

    elif estado == 'dormido':
        oled.hline(cx - 15, cy + 3, 31, 1)
        oled.hline(cx - 15, cy + 4, 31, 1)
        draw_brow(cx, cy, 4, 0, offset_y=-5)

    elif estado == 'durmiendo':
        dy_breath = int(math.sin(t * 0.0015) * 2.0)
        bx = cx
        by = cy + 2 + dy_breath
        oled.line(bx - 14, by + 4, bx - 7, by, 1)
        oled.line(bx - 7, by, bx + 7, by, 1)
        oled.line(bx + 7, by, bx + 14, by + 4, 1)
        oled.line(bx - 14, by + 5, bx - 7, by + 1, 1)
        oled.line(bx - 7, by + 1, bx + 7, by + 1, 1)
        oled.line(bx + 7, by + 1, bx + 14, by + 5, 1)
        draw_arch_brow(cx, cy + 2 + dy_breath, 8, offset_y=-6, peak_height=3)

    elif estado == 'procesando':
        fill_superellipse(cx, cy, rx_eff, ry_current)
        draw_squircle_pupil(cx, rx_eff, ry_current,
                            cx + px_off, cy + py_off)
        scan_y = cy - ry_current + int(((math.sin(t * 0.005) + 1.0) / 2.0) * (ry_current * 2))
        if cy - ry_current <= scan_y <= cy + ry_current:
            oled.hline(cx - rx_eff - 2, scan_y, rx_eff * 2 + 5, 0)
        draw_brow(cx, cy, ry, 0)

    elif estado == 'error':
        glitch_t = t % 1000
        if glitch_t < 800:
            for d in range(-10, 11):
                oled.pixel(cx + d, cy + d, 1)
                oled.pixel(cx + d, cy - d, 1)
                oled.pixel(cx + d + 1, cy + d, 1)
                oled.pixel(cx + d - 1, cy + d, 1)
        else:
            oled.fill_rect(cx - rx_eff, cy - 3, rx_eff * 2, 7, 1)
        draw_brow(cx + random.randint(-2, 2), cy, ry,
                  random.uniform(-0.5, 0.5))

    elif estado == 'siguiendo':
        # ═══ FASE 6: tracking suavizado + acquisition gesture ═══
        # Si el tracker manda offsets reales, usar smoothing (track_dx/dy_smooth).
        # Si no, fallback al demo circular para verlo standalone.
        if beh.sig_dx != 0.0 or beh.sig_dy != 0.0 or beh.track_dx_smooth != 0.0:
            dx = int(beh.track_dx_smooth)
            dy = int(beh.track_dy_smooth)
        else:
            dx = int(math.cos(t * 0.002) * 7)
            dy = int(math.sin(t * 0.002) * 5)

        # Acquisition gesture: ojos se agrandan brevemente al detectar cara
        acq_rx_boost = 0
        acq_ry_boost = 0
        acq_brow_lift = 0
        if beh.acq_active:
            elapsed = t - beh.acq_start_t
            # Rise rápido los primeros 150ms, decay después
            if elapsed < 150:
                phase = elapsed / 150.0
            else:
                phase = 1.0 - (elapsed - 150) / max(1, _ACQ_DURATION_MS - 150)
                if phase < 0.0: phase = 0.0
            acq_rx_boost = int(_ACQ_PEAK_RX * phase)
            acq_ry_boost = int(_ACQ_PEAK_RY * phase)
            acq_brow_lift = int(2 * phase)  # cejas suben un poco también

        rx_track = rx_eff + acq_rx_boost
        ry_track = ry_current + acq_ry_boost

        fill_superellipse(cx, cy, rx_track, ry_track)
        draw_squircle_pupil(cx, rx_track, ry_track,
                            cx + dx + px_off, cy + dy + py_off)
        draw_brow(cx, cy, ry, 0, offset_y=-4 - acq_brow_lift)

    elif estado == 'hablando':
        # ═══ FASE 5: SIN BOCA — toda la expresividad vive en los ojos ═══
        #
        # Capas combinadas:
        #   a) "envelope" sintético (suma de senoidales rápida + lenta + jitter)
        #      simula el ritmo de sílabas y frases.
        #   b) Pulsación del ojo en eje vertical con el envelope.
        #   c) Pupila con bounce sutil + engagement lateral (Bob "mira al oyente").
        #   d) Cejas que se levantan en momentos de énfasis (envelope alto).
        #   e) Asimetría L/R en la altura de las cejas para que se vea natural.
        #   f) Smile-eye ocasional cada ~6 s (mitad inferior cubierta).

        # a) Envelope (0..1 aprox.) con jitter por instancia
        beat_fast = abs(math.sin(t * 0.020 + beh.speech_jitter_a))
        beat_slow = abs(math.sin(t * 0.007 + beh.speech_jitter_b))
        envelope = beat_fast * 0.65 + beat_slow * 0.35
        envelope += random.uniform(-0.05, 0.05)
        if envelope < 0.0: envelope = 0.0
        if envelope > 1.0: envelope = 1.0

        # b) Pulsación vertical
        ry_speak = ry_current + int(envelope * 2)

        # c) Pupila: bounce vertical sutil + leve engagement lateral
        pupil_bounce_y = int(math.sin(t * 0.014) * 1.2)
        pupil_engage_x = int(math.cos(t * 0.006) * 2)

        # d/e) Cejas: lift por envelope + asimetría L/R orgánica
        brow_lift = int(envelope * 4)
        asym = int(math.sin(t * 0.003 + (0.0 if side == 'L' else 1.6)) * 1.5)
        brow_total_offset = -4 - brow_lift - asym

        # f) Smile-eye window: ~0.5 s cada ~6 s
        smile_phase = (t // 60) % 100   # 6 s ciclo
        is_smile_window = 75 <= smile_phase < 83

        if is_smile_window:
            # Mitad inferior cubierta → sonrisa con los ojos
            fill_superellipse(cx, cy, rx_eff, max(8, ry_speak - 1))
            fill_ellipse(cx, cy + 8, rx_eff + 3, 5, 0)
            draw_arch_brow(cx, cy, ry, offset_y=brow_total_offset, peak_height=3)
        else:
            fill_superellipse(cx, cy, rx_eff, ry_speak)
            draw_squircle_pupil(cx, rx_eff, ry_speak,
                                cx + pupil_engage_x + px_off,
                                cy + pupil_bounce_y + py_off)
            draw_brow(cx, cy, ry, 0, offset_y=brow_total_offset)

    elif estado == 'amor':
        pulse = math.sin(t * 0.006) * 2.0
        fill_heart(cx, cy, int(16 + pulse))
        draw_arch_brow(cx, cy, 12, offset_y=-6, peak_height=4)


# ============================================================
# RENDER PRINCIPAL
# ============================================================
def render(e=None):
    """Dibuja un frame completo según el estado actual."""
    if e is not None:
        beh.estado = e

    oled.fill(0)
    t = _get_t()

    # Actualizar todas las capas de comportamiento
    update_boredom(t)     # Fase 7/8 — puede cambiar beh.estado a 'durmiendo'
    update_blinks(t)
    update_wink(t)
    update_saccade(t)
    update_initiative(t)
    update_quirk(t)
    update_attention(t)

    qm = _quirk_modifiers(t)

    LX, LY, RX, RY = 32, 32, 96, 32
    base_rx, base_ry = 22, 18

    _draw_eye(LX, LY, base_rx, base_ry, 'L', eye_l, t, qm)
    _draw_eye(RX, RY, base_rx, base_ry, 'R', eye_r, t, qm)

    # FASE 8: Zzz flotantes cuando Bob duerme. Se dibujan UNA sola vez
    # (no per-ojo) después de los ojos para no duplicarlas.
    if beh.estado == 'durmiendo':
        _draw_zzz(t)


# ============================================================
# ZZZ FLOTANTES (Fase 8 — visible durante 'durmiendo')
# ============================================================
def _draw_single_z(x, y, size):
    """Dibuja una Z de tamaño `size` con 3 trazos: top, diagonal, bottom."""
    if y < 0 or y + size > 64: return
    if x < 0 or x + size > 128: return
    # Trazo superior
    oled.hline(x, y, size, 1)
    # Diagonal de arriba-derecha a abajo-izquierda
    for k in range(size):
        oled.pixel(x + size - 1 - k, y + k, 1)
    # Trazo inferior
    oled.hline(x, y + size - 1, size, 1)


def _draw_zzz(t):
    """
    3 Z's flotando arriba-derecha. Cada una sube y crece con su propio ciclo
    desfasado para que parezcan emerger continuamente — efecto cómic clásico.
    """
    cycle_ms = 2400
    for i in range(3):
        # Cada Z desfasada 1/3 de ciclo para que siempre haya alguna visible
        phase = (((t + i * (cycle_ms // 3)) % cycle_ms)) / cycle_ms  # 0..1
        # Crece de 3 a 7 px y sube de y=30 a y=2
        size = 3 + i + int(phase * 2)
        y    = int(30 - phase * 28)
        x    = int(82 + i * 6 + phase * 8)
        # Ventana de visibilidad: aparece a 8%, desaparece a 92% del ciclo
        if 0.08 < phase < 0.92 and y >= 0:
            _draw_single_z(x, y, size)


# ============================================================
# API DE DEBUG: forzar quirks/inicativas para probar
# ============================================================
def force_quirk(name):
    """Dispara un quirk específico ya (testing)."""
    if name not in ('startle', 'wander', 'sneeze', 'double_take'):
        return
    t = _get_t()
    beh.quirk_type = name
    beh.quirk_start_t = t
    beh.quirk_duration = {
        'startle': 500, 'wander': 1600, 'sneeze': 350, 'double_take': 900,
    }[name]


def force_wink(eye='L'):
    """Dispara un guiño en el ojo dado (testing)."""
    t = _get_t()
    target = eye_l if eye == 'L' else eye_r
    if not target.blink_active:
        target.start_blink(t, is_double=False, is_wink=True)


def force_acquisition():
    """Dispara el gesto de acquisition (testing Fase 6)."""
    beh.acq_active  = True
    beh.acq_start_t = _get_t()


def force_search():
    """Dispara el gesto de search (testing Fase 6)."""
    beh.search_active  = True
    beh.search_start_t = _get_t()


def force_boredom_level(level):
    """Override del nivel de aburrimiento (0-5). Para demo. None desactiva."""
    if level is not None:
        level = max(0, min(5, int(level)))
    beh._boredom_override = level


def reset_boredom():
    """Resetea el aburrimiento (como si Bob acabara de interactuar)."""
    beh.last_interaction_t = _get_t()
    beh.boredom_level = 0
    beh._boredom_override = None
    beh.sleep_transition_active = False


# ============================================================
# API COMPAT con Esp32/oled_ojos.py (para reemplazo transparente)
# ============================================================
def set_estado(s):
    """
    Setea el estado actual. Acepta:
      - Nombres lowercase locales ('neutral', 'feliz', 'hablando', ...)
      - Nombres UPPERCASE del SerialManager ('ESPERANDO', 'HABLANDO', ...)
    """
    beh.estado = PRODUCTION_STATES.get(s, s)


def set_following_offset(dx, dy):
    """
    Actualiza el offset normalizado [-1..1] de la pupila en 'siguiendo'.
    Lo llama el firmware main cuando recibe 'SIGUIENDO:dx,dy' por serial.
    """
    beh.sig_dx = float(dx)
    beh.sig_dy = float(dy)


def reset():
    """Resetea el estado interno. Útil tras reconectar serial."""
    eye_l.blink_active = False
    eye_r.blink_active = False
    beh.estado = 'neutral'
    beh.sig_dx = 0.0
    beh.sig_dy = 0.0
    beh.quirk_type = None
    beh.initiative_active = False
    beh.saccade_active = False


def tick(estado='ESPERANDO', sig_dx=0.0, sig_dy=0.0):
    """
    Entrada principal compatible con la firmware actual (Esp32/oled_ojos.py).
    El main loop del firmware llama tick() en cada ciclo.

    Equivalente a:
        set_estado(estado)
        set_following_offset(sig_dx, sig_dy)
        render()
        oled.show()
    """
    set_estado(estado)
    set_following_offset(sig_dx, sig_dy)
    render()
    oled.show()


# Shortcuts equivalentes al firmware viejo
def ojos_normal():           tick('ESPERANDO')
def ojos_escuchar():         tick('ESCUCHANDO')
def ojos_pensar():           tick('PENSANDO')
def ojos_hablar():           tick('HABLANDO')
def ojos_feliz():            tick('FELIZ')
def ojos_curioso():          tick('CURIOSO')
def ojos_siguiendo(dx, dy):  tick('SIGUIENDO', dx, dy)


# ============================================================
# DEMO INTERACTIVA
# ============================================================
def show_emotion_demo(e):
    """Cicla animando una sola emoción por 3.5 segundos."""
    idx = ESTADOS.index(e) + 1
    print("Mostrando gesto %d/%d: %s" % (idx, len(ESTADOS), e.upper()))
    start_time = _get_t()
    while _get_t() - start_time < 3500:
        render(e)
        oled.show()
        try:
            time.sleep_ms(50)
        except AttributeError:
            time.sleep(0.05)


def show_phase4_demo():
    """
    Demo de Fase 4: mantiene a Bob en 'neutral' y muestra el comportamiento
    autónomo durante 60 segundos. También fuerza un quirk de cada tipo
    en los últimos 20 segundos para verificarlos.
    """
    print("=" * 50)
    print("FASE 4 — DEMO DE PERSONALIDAD")
    print("Observa por 60 s. Bob debería:")
    print("  - Parpadear naturalmente (cada 2-6 s)")
    print("  - Hacer microsacadas (jitter sutil)")
    print("  - Hacer miradas espontáneas más largas")
    print("  - Mirar de pronto a una dirección (iniciativa)")
    print("  - Guiñar ocasionalmente")
    print("  - Hacer una reacción inesperada o más")
    print("=" * 50)
    start_time = _get_t()
    # Acelerar los quirks para que se vean en la demo
    beh.next_quirk_t = _get_t() + 8000

    forced_quirks = [
        (40000, 'startle'),
        (45000, 'double_take'),
        (50000, 'sneeze'),
        (55000, 'wander'),
    ]
    fired = set()

    while _get_t() - start_time < 60000:
        render('neutral')
        oled.show()
        # Forzar quirks específicos en momentos definidos para demo
        elapsed = _get_t() - start_time
        for trigger_t, qname in forced_quirks:
            if elapsed >= trigger_t and qname not in fired:
                fired.add(qname)
                if beh.quirk_type is None:
                    force_quirk(qname)
                    print("  [%d s] forzando quirk: %s" % (elapsed // 1000, qname))
        try:
            time.sleep_ms(50)
        except AttributeError:
            time.sleep(0.05)


def show_phase5_demo():
    """
    Demo de Fase 5: Bob en 'hablando' por 30 s.
    Sin boca — los ojos cargan toda la expresividad:
      - Pulsación leve al ritmo de 'sílabas'
      - Pupila con bounce + engagement lateral
      - Cejas que se levantan en momentos de énfasis (con asimetría L/R)
      - Smile-eye ocasional (~cada 6 s, ventana ~0.5 s)
      - Parpadeos más frecuentes que en reposo (1.4-3.2 s)
      - Microsacadas activas para sostener la mirada
    """
    print("=" * 50)
    print("FASE 5 — SISTEMA DE HABLA (sin boca, ojos cargan todo)")
    print("Observa por 30 s. Bob debería:")
    print("  - Pulsar levemente al ritmo de habla")
    print("  - Tener bounces sutiles de pupila")
    print("  - Levantar cejas en énfasis (asimétrico L/R)")
    print("  - Hacer un 'smile-eye' ocasional")
    print("  - Parpadear más seguido que en neutral")
    print("=" * 50)
    start_time = _get_t()
    while _get_t() - start_time < 30000:
        render('hablando')
        oled.show()
        try:
            time.sleep_ms(50)
        except AttributeError:
            time.sleep(0.05)
    print("Fase 5 demo terminada.\n")


def show_phase6_demo():
    """
    Demo Fase 6: sistema de atención completo.

    Simula 3 ciclos de:
      1. neutral 2s (sin cara)
      2. cara aparece → acquisition (ojos se agrandan) + tracking suave
      3. cara se mueve en círculo → tracking sigue
      4. cara desaparece → search (escaneo lateral con amplitud decreciente)
      5. vuelve a neutral exploratorio
    """
    print("=" * 50)
    print("FASE 6 — SISTEMA DE ATENCIÓN")
    print("3 ciclos: detección → tracking → pérdida → search.")
    print("Mirá cómo los ojos cambian SOLOS en cada transición.")
    print("=" * 50)

    for cycle in range(3):
        print("\n--- Ciclo %d/3 ---" % (cycle + 1))

        # 1. neutral 2s
        print("  [esperando — sin cara]")
        start = _get_t()
        while _get_t() - start < 2000:
            tick('ESPERANDO')
            try: time.sleep_ms(50)
            except AttributeError: time.sleep(0.05)

        # 2-3. cara aparece y se mueve 4s
        print("  [cara detectada → acquisition + tracking suave]")
        start = _get_t()
        while _get_t() - start < 4000:
            elapsed = (_get_t() - start) / 1000.0
            sig_dx = math.cos(elapsed * 1.2) * 0.55
            sig_dy = math.sin(elapsed * 1.0) * 0.35
            tick('SIGUIENDO', sig_dx, sig_dy)
            try: time.sleep_ms(50)
            except AttributeError: time.sleep(0.05)

        # 4-5. cara desaparece → search se gatilla automáticamente
        print("  [cara perdida → search + vuelta a exploratorio]")
        start = _get_t()
        while _get_t() - start < 3500:
            tick('ESPERANDO')
            try: time.sleep_ms(50)
            except AttributeError: time.sleep(0.05)

    print("\nFase 6 demo terminada.\n")


def show_phase7_demo():
    """
    Demo Fase 7: aburrimiento escalonado.

    Mantenemos a Bob en 'neutral' y forzamos manualmente cada nivel por 4 s
    para que se vea progresivamente más cansado, sin esperar los 5 minutos
    reales que tardaría naturalmente. Después suelta el override y vuelve.
    """
    print("=" * 50)
    print("FASE 7 — SISTEMA DE ABURRIMIENTO")
    print("Forzamos cada nivel 4 s para mostrar la escalada:")
    print("  0 normal -> 1 mirar -> 2 pensativo ->")
    print("  3 aburrido -> 4 somnoliento")
    print("(nivel 5 → sleep transition, va en Fase 8)")
    print("=" * 50)

    for lvl in range(5):
        print("  nivel %d" % lvl)
        force_boredom_level(lvl)
        start = _get_t()
        while _get_t() - start < 4000:
            render('neutral')
            oled.show()
            try: time.sleep_ms(50)
            except AttributeError: time.sleep(0.05)

    # Volver a normal y resetear timer
    force_boredom_level(None)
    reset_boredom()
    print("Fase 7 demo terminada.\n")


def show_phase8_demo():
    """
    Demo Fase 8: transición a sueño + 'durmiendo' con Zzz.

    Forzamos boredom level 4 (somnoliento) por 3 s, después saltamos al 5
    para gatillar la transición, observamos el cierre gradual (3 s)
    y después dejamos correr 'durmiendo' (Zzz) por 8 s.
    """
    print("=" * 50)
    print("FASE 8 — TRANSICIÓN DE SUEÑO")
    print("  1. Somnoliento 3 s")
    print("  2. Cierre gradual 3 s (Fase 8 propiamente)")
    print("  3. Durmiendo con Zzz 8 s")
    print("=" * 50)

    # 1. Somnoliento
    force_boredom_level(4)
    start = _get_t()
    while _get_t() - start < 3000:
        render('neutral')
        oled.show()
        try: time.sleep_ms(50)
        except AttributeError: time.sleep(0.05)

    # 2. Disparar transición
    print("  → durmiéndose...")
    force_boredom_level(5)
    start = _get_t()
    while _get_t() - start < _SLEEP_TRANSITION_MS + 200:
        render('neutral')  # update_boredom cambia a 'durmiendo' al terminar
        oled.show()
        try: time.sleep_ms(50)
        except AttributeError: time.sleep(0.05)

    # 3. Durmiendo (Zzz)
    print("  → Zzz")
    start = _get_t()
    while _get_t() - start < 8000:
        render('durmiendo')
        oled.show()
        try: time.sleep_ms(50)
        except AttributeError: time.sleep(0.05)

    # Reset
    force_boredom_level(None)
    reset_boredom()
    print("Fase 8 demo terminada.\n")


def show_phase9_showcase():
    """
    FASE 9 — Showcase completo. Tour guiado de todo el sistema.

    7 secciones, cada una anunciada por consola:
      1. Las 21 emociones (3.5 s cada una, total ~75 s)
      2. Microexpresiones autónomas (Fase 3 — neutral 25 s)
      3. Personalidad / quirks forzados (Fase 4 — 30 s)
      4. Habla expresiva sin boca (Fase 5 — 25 s)
      5. Atención: 2 ciclos completos (Fase 6 — ~25 s)
      6. Aburrimiento escalonado (Fase 7 — ~20 s)
      7. Sueño con Zzz (Fase 8 — ~14 s)
    Total ~3 min 35 s. Bob queda durmiendo al final.
    """
    print()
    print("#" * 50)
    print("#  MODO SHOWCASE — Tour completo de Bob")
    print("#  ~3 min 35 s de demostración guiada")
    print("#" * 50)

    # ── 1. Las 21 emociones ──────────────────────────────────────
    print("\n[1/7] Las 21 emociones de Bob")
    for e in ESTADOS:
        show_emotion_demo(e)
    reset_boredom()

    # ── 2. Microexpresiones autónomas ────────────────────────────
    print("\n[2/7] Microexpresiones autónomas — neutral 25 s")
    start = _get_t()
    while _get_t() - start < 25000:
        render('neutral')
        oled.show()
        try: time.sleep_ms(50)
        except AttributeError: time.sleep(0.05)

    # ── 3. Personalidad — quirks forzados ────────────────────────
    print("\n[3/7] Personalidad — los 4 quirks (Fase 4)")
    reset_boredom()
    for qname in ('startle', 'double_take', 'sneeze', 'wander'):
        print("    quirk: %s" % qname)
        force_quirk(qname)
        start = _get_t()
        while _get_t() - start < 6000:
            render('neutral')
            oled.show()
            try: time.sleep_ms(50)
            except AttributeError: time.sleep(0.05)
        force_wink('L')

    # ── 4. Habla expresiva ───────────────────────────────────────
    print("\n[4/7] Habla expresiva sin boca (Fase 5)")
    reset_boredom()
    start = _get_t()
    while _get_t() - start < 25000:
        render('hablando')
        oled.show()
        try: time.sleep_ms(50)
        except AttributeError: time.sleep(0.05)

    # ── 5. Atención — 2 ciclos completos ─────────────────────────
    print("\n[5/7] Sistema de atención (Fase 6) — 2 ciclos")
    reset_boredom()
    for cycle in range(2):
        start = _get_t()
        while _get_t() - start < 2000:
            tick('ESPERANDO')
            try: time.sleep_ms(50)
            except AttributeError: time.sleep(0.05)
        start = _get_t()
        while _get_t() - start < 4500:
            elapsed = (_get_t() - start) / 1000.0
            sig_dx = math.cos(elapsed * 1.1) * 0.5
            sig_dy = math.sin(elapsed * 0.9) * 0.35
            tick('SIGUIENDO', sig_dx, sig_dy)
            try: time.sleep_ms(50)
            except AttributeError: time.sleep(0.05)
        start = _get_t()
        while _get_t() - start < 3000:
            tick('ESPERANDO')
            try: time.sleep_ms(50)
            except AttributeError: time.sleep(0.05)

    # ── 6. Aburrimiento escalonado (acelerado) ───────────────────
    print("\n[6/7] Aburrimiento escalonado (Fase 7)")
    reset_boredom()
    for lvl in range(5):
        force_boredom_level(lvl)
        start = _get_t()
        while _get_t() - start < 3500:
            render('neutral')
            oled.show()
            try: time.sleep_ms(50)
            except AttributeError: time.sleep(0.05)

    # ── 7. Sueño con Zzz ─────────────────────────────────────────
    print("\n[7/7] Transición a sueño (Fase 8)")
    force_boredom_level(5)
    start = _get_t()
    while _get_t() - start < _SLEEP_TRANSITION_MS + 200:
        render('neutral')
        oled.show()
        try: time.sleep_ms(50)
        except AttributeError: time.sleep(0.05)
    start = _get_t()
    while _get_t() - start < 8000:
        render('durmiendo')
        oled.show()
        try: time.sleep_ms(50)
        except AttributeError: time.sleep(0.05)

    print()
    print("#" * 50)
    print("#  SHOWCASE COMPLETO — Bob ahora duerme tranquilo")
    print("#" * 50)
    print()

    # Reset final
    force_boredom_level(None)
    reset_boredom()


def run_demo():
    """Demo infinita: emociones + transiciones + Fase 4 + Fase 5 + Fase 6 + 7 + 8."""
    while True:
        # 1. Tour por cada emoción (incluye 'hablando')
        for e in ESTADOS:
            show_emotion_demo(e)

        # 2. Transiciones aleatorias rápidas
        for _ in range(5):
            e = random.choice(ESTADOS)
            idx = ESTADOS.index(e) + 1
            print("Transición - Gesto %d/%d: %s" % (idx, len(ESTADOS), e.upper()))
            start_time = _get_t()
            while _get_t() - start_time < 2000:
                render(e)
                oled.show()
                try:
                    time.sleep_ms(50)
                except AttributeError:
                    time.sleep(0.05)

        # 3. Bloque de Fase 4 — observación pasiva
        show_phase4_demo()

        # 4. Bloque de Fase 5 — sistema de habla
        show_phase5_demo()

        # 5. Bloque de Fase 6 — sistema de atención
        show_phase6_demo()

        # 6. Bloque de Fase 7 — aburrimiento escalonado
        show_phase7_demo()

        # 7. Bloque de Fase 8 — transición a sueño con Zzz
        show_phase8_demo()


if __name__ == '__main__':
    # show_phase5_demo()
    # show_phase6_demo()
    # show_phase7_demo()
    # show_phase8_demo()
    show_phase9_showcase()  # tour completo de ~3m35s — descomentar si querés
