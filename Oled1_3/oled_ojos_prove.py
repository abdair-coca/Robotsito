# oled_ojos_prove.py — Sistema de Ojos Emocionales para Bob (Creeper)
# Diseño elegido: "Futuristas" (squircles / superelipses).
# Optimizado para pantalla OLED SH1106 de 128x64 sobre MicroPython/ESP32.
#
# Especificaciones:
#   - Ojos GRANDES sin boca.
#   - Expresividad mejorada mediante cejas (angulares y arqueadas).
#   - Pupilas en forma de squircles concéntricos pequeños con brillo/glint.

from machine import Pin, I2C
from sh1106 import SH1106
import time
import math
import random

# Inicialización de hardware
i2c = I2C(0, scl=Pin(22), sda=Pin(21), freq=100000)
oled = SH1106(128, 64, i2c)

# Estado global
estado = 'neutral'

# Variables de control para microexpresiones (Fase 3)
blink_active = False
blink_start_t = 0
blink_is_double = False
next_blink_t = 3000

saccade_dx = 0
saccade_dy = 0
next_saccade_t = 1500
saccade_end_t = 0
saccade_active = False

# Listado completo de emociones de la Fase 2
ESTADOS = [
    'neutral', 'feliz', 'muy_feliz', 'escuchando', 'pensando', 
    'curioso', 'sorprendido', 'confundido', 'triste', 'muy_triste', 
    'enojado', 'sospechando', 'travieso', 'orgulloso', 'dormido', 
    'durmiendo', 'procesando', 'error', 'siguiendo', 'amor'
]

# ============================================================
# HELPERS DE ANIMACIÓN Y TIEMPO
# ============================================================

def _get_t():
    """Retorna milisegundos de manera compatible con MicroPython y PC."""
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.time() * 1000)

def get_blink_factor(t):
    """Calcula el factor de párpados (0.0 a 1.0) y gestiona los tiempos del parpadeo."""
    global blink_active, blink_start_t, blink_is_double, next_blink_t
    
    if not blink_active and t >= next_blink_t:
        blink_active = True
        blink_start_t = t
        blink_is_double = random.random() < 0.15
        
    if not blink_active:
        return 0.0
        
    dt = t - blink_start_t
    if dt < 60:
        return dt / 60.0 # Cerrando
    elif dt < 100:
        return 1.0 # Cerrado
    elif dt < 160:
        return 1.0 - (dt - 100) / 60.0 # Abriendo
    else:
        blink_active = False
        if blink_is_double:
            blink_is_double = False
            blink_active = True
            blink_start_t = t
            return 0.0
        else:
            next_blink_t = t + random.randint(2000, 6000)
            return 0.0

def update_saccade(t):
    """Actualiza sacadas y miradas espontáneas de forma impredecible."""
    global next_saccade_t, saccade_dx, saccade_dy, saccade_end_t, saccade_active
    
    if t >= next_saccade_t:
        r = random.random()
        if r < 0.25:
            # Mirada espontánea (desplazamiento medio-largo)
            saccade_dx = random.choice([-5, -3, 3, 5])
            saccade_dy = random.choice([-2, -1, 1, 2])
            duration = random.randint(500, 1200)
            next_saccade_t = t + duration + random.randint(1500, 4000)
            saccade_end_t = t + duration
            saccade_active = True
        elif r < 0.75:
            # Microsacada rápida (jitter sutil)
            saccade_dx = random.choice([-1, 0, 1])
            saccade_dy = random.choice([-1, 0, 1])
            duration = random.randint(80, 200)
            next_saccade_t = t + duration + random.randint(400, 1500)
            saccade_end_t = t + duration
            saccade_active = True
        else:
            next_saccade_t = t + random.randint(800, 2500)
            
    if saccade_active and t >= saccade_end_t:
        saccade_dx = 0
        saccade_dy = 0
        saccade_active = False

# ============================================================
# HELPERS GEOMÉTRICOS OPTIMIZADOS
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
    """Dibuja un squircle (superelipse con n=4). Optimizado con sqrt dobles."""
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
    """Dibuja un corazón relleno combinando círculos y un triángulo."""
    fill_circle(cx - r // 2, cy - r // 3, r // 2, color)
    fill_circle(cx + r // 2, cy - r // 3, r // 2, color)
    pts = [(cx - r, cy - r // 3), (cx + r, cy - r // 3), (cx, cy + r)]
    fill_polygon(pts, color)

# ============================================================
# DIBUJO DE CEJAS EXPRESIVAS (BROWS)
# ============================================================

def draw_brow(cx, cy_base, ry, angle, offset_y=-4):
    """Dibuja una ceja recta inclinada con grosor de 2px."""
    y_center = cy_base - ry + offset_y
    if y_center < 1: return
    half_w = 16
    dy = int(angle * 5)
    
    # Left / Right side symmetry
    if cx < 64: # Izquierdo
        x1, y1 = cx - half_w, y_center + dy
        x2, y2 = cx + half_w, y_center - dy
    else:       # Derecho
        x1, y1 = cx - half_w, y_center - dy
        x2, y2 = cx + half_w, y_center + dy
        
    oled.line(x1, y1, x2, y2, 1)
    oled.line(x1, y1 + 1, x2, y2 + 1, 1)

def draw_arch_brow(cx, cy_base, ry, offset_y=-4, peak_height=4):
    """Dibuja una ceja arqueada (pico feliz) con grosor de 2px."""
    y_base = cy_base - ry + offset_y
    if y_base < peak_height + 1: return
    half_w = 16
    
    # Dibujar pico izquierdo y derecho
    oled.line(cx - half_w, y_base, cx, y_base - peak_height, 1)
    oled.line(cx, y_base - peak_height, cx + half_w, y_base, 1)
    oled.line(cx - half_w, y_base + 1, cx, y_base - peak_height + 1, 1)
    oled.line(cx, y_base - peak_height + 1, cx + half_w, y_base + 1, 1)

# ============================================================
# RENDERIZADO DEL ROSTRO COMPLETO (SÓLO OJOS)
# ============================================================

def render(e=None):
    """Dibuja un frame del rostro animado según el estado."""
    global estado
    if e is not None:
        estado = e
        
    oled.fill(0)
    t = _get_t()
    
    LX, LY, RX, RY = 32, 32, 96, 32
    base_rx, base_ry = 22, 18
    
    # 1. Parpadeo sutil (Microexpresión)
    puedo_parpadear = estado not in ('dormido', 'durmiendo', 'orgulloso', 'error', 'amor')
    lid_factor = get_blink_factor(t) if puedo_parpadear else 0.0
    
    # 2. Sacadas / miradas sutiles (Microexpresión)
    puedo_sacar = estado in ('neutral', 'escuchando', 'pensando', 'curioso', 'sorprendido', 'sospechando', 'procesando', 'siguiendo')
    if puedo_sacar:
        update_saccade(t)
    else:
        global saccade_dx, saccade_dy
        saccade_dx, saccade_dy = 0, 0
    
    def draw_eye(cx, cy, rx, ry, side):
        # Escalar el alto vertical si hay parpadeo
        ry_current = int(ry * (1.0 - lid_factor)) if lid_factor > 0.0 else ry
        
        # Si está completamente cerrado (o casi), dibujar la línea horizontal
        if ry_current <= 2:
            oled.hline(cx - 15, cy + 3, 31, 1)
            oled.hline(cx - 15, cy + 4, 31, 1)
            draw_brow(cx, cy, 4, 0, offset_y=-5)
            return

        # Ayudante para dibujar pupila squircle con brillo
        def draw_squircle_pupil(px, py, prx=8, pry=6):
            if ry_current < pry + 2: return # No dibujar pupila si no hay espacio
            # Limitar pupila dentro del globo blanco actual (ry_current)
            if px < cx - rx + prx: px = cx - rx + prx
            if px > cx + rx - prx: px = cx + rx - prx
            if py < cy - ry_current + pry: py = cy - ry_current + pry
            if py > cy + ry_current - pry: py = cy + ry_current - pry
            fill_superellipse(px, py, prx, pry, n=4, color=0)
            # Brillo de pupila (glint) de 2px
            oled.pixel(px - 2, py - 2, 1)
            oled.pixel(px - 1, py - 2, 1)

        if estado == 'neutral':
            dx = int(math.sin(t * 0.001) * 1.5)
            dy = int(math.cos(t * 0.0015) * 0.8)
            fill_superellipse(cx + dx, cy + dy, rx, ry_current)
            draw_squircle_pupil(cx + dx + saccade_dx, cy + dy + saccade_dy)
            draw_brow(cx, cy, ry, 0)
        elif estado == 'feliz':
            dy_bounce = int(abs(math.sin(t * 0.004)) * 3)
            fill_superellipse(cx, cy - dy_bounce, rx, ry_current)
            fill_ellipse(cx, cy - dy_bounce + 10, rx + 4, ry_current - 3, 0)
            draw_arch_brow(cx, cy - dy_bounce, ry, offset_y=-5, peak_height=3)
        elif estado == 'muy_feliz':
            dy_bounce = int(abs(math.sin(t * 0.006)) * 5)
            fill_superellipse(cx, cy - dy_bounce, rx, ry_current - 2)
            fill_ellipse(cx, cy - dy_bounce + 6, rx + 4, ry_current - 4, 0)
            by = cy - dy_bounce + ry_current + 2
            if by < 63:
                for bx in (cx - 14, cx - 9, cx + 9, cx + 14):
                    oled.pixel(bx, by, 1)
                    oled.pixel(bx + 1, by - 1, 1)
            draw_arch_brow(cx, cy - dy_bounce, ry - 2, offset_y=-7, peak_height=5)
        elif estado == 'escuchando':
            pulse = math.sin(t * 0.005) * 1.0
            fill_superellipse(cx, cy - 2, int(rx + pulse), ry_current + 2)
            draw_squircle_pupil(cx + saccade_dx, cy - 2 + saccade_dy)
            draw_brow(cx, cy - 2, ry + 2, 0, offset_y=-7)
        elif estado == 'pensando':
            fill_superellipse(cx, cy, rx, ry_current)
            px, py = 5, -4
            draw_squircle_pupil(cx + px + saccade_dx, cy + py + saccade_dy, 7, 5)
            if side == 'L':
                oled.fill_rect(cx - rx - 1, cy - ry_current - 1, 2*rx + 3, ry_current // 2, 0)
                draw_brow(cx, cy, ry, -0.6)
            else:
                oled.fill_rect(cx - rx - 1, cy - ry_current - 1, 2*rx + 3, ry_current // 3, 0)
                draw_brow(cx, cy, ry, 0.4, offset_y=-8)
        elif estado == 'curioso':
            if side == 'L':
                h_wobble = int(math.sin(t * 0.003) * 2)
                fill_superellipse(cx, cy - 3, rx, ry_current + 3 + h_wobble)
                draw_squircle_pupil(cx + saccade_dx, cy - 3 + saccade_dy)
                draw_brow(cx, cy - 3, ry + 3 + h_wobble, 0.3, offset_y=-9)
            else:
                h_wobble = int(math.cos(t * 0.003) * 2)
                fill_superellipse(cx, cy + 2, rx - 3, ry_current - 3 + h_wobble)
                draw_squircle_pupil(cx + saccade_dx, cy + 2 + saccade_dy, 6, 5)
                draw_brow(cx, cy + 2, ry - 3 + h_wobble, -0.4, offset_y=-3)
        elif estado == 'sorprendido':
            shake_x = random.randint(-1, 1) if t % 2 == 0 else 0
            shake_y = random.randint(-1, 1) if t % 3 == 0 else 0
            fill_superellipse(cx + shake_x, cy + shake_y - 2, rx - 2, ry_current + 4)
            draw_squircle_pupil(cx + shake_x + saccade_dx, cy + shake_y - 2 + saccade_dy, 4, 3)
            draw_arch_brow(cx + shake_x, cy + shake_y - 2, ry + 4, offset_y=-9, peak_height=4)
        elif estado == 'confundido':
            fill_superellipse(cx, cy, rx - 2, ry_current - 2)
            if side == 'L':
                pts = [(cx - rx, cy - ry_current), (cx + 2, cy - ry_current), (cx - rx, cy + 2)]
                fill_polygon(pts, 0)
                draw_brow(cx, cy, ry - 2, 0.5)
            else:
                pts_r = [(cx - rx, cy - ry_current), (cx + 2, cy - ry_current), (cx - rx, cy + 3)]
                fill_polygon(pts_r, 0)
                draw_brow(cx, cy, ry - 2, -0.6)
        elif estado == 'triste':
            fill_superellipse(cx, cy, rx, ry_current)
            if side == 'L':
                pts = [(cx - rx - 2, cy - ry_current - 2), (cx, cy - ry_current - 2), (cx - rx - 2, cy)]
                fill_polygon(pts, 0)
            else:
                pts = [(cx + rx + 2, cy - ry_current - 2), (cx, cy - ry_current - 2), (cx + rx + 2, cy)]
                fill_polygon(pts, 0)
            draw_squircle_pupil(cx, cy + 3, 8, 5)
            draw_brow(cx, cy, ry, 0.7)
            if side == 'L':
                tear_dy = int((t * 0.015) % 22)
                tx, ty = cx - rx + 3, cy + 3 + tear_dy
                if ty < 62:
                    fill_circle(tx, ty, 2, 1)
                    oled.pixel(tx, ty - 2, 1)
        elif estado == 'muy_triste':
            fill_superellipse(cx, cy - 1, rx, ry_current - 2)
            if side == 'L':
                pts = [(cx - rx - 2, cy - ry_current - 3), (cx + 3, cy - ry_current - 3), (cx - rx - 2, cy + 2)]
                fill_polygon(pts, 0)
            else:
                pts = [(cx + rx + 2, cy - ry_current - 3), (cx - 3, cy - ry_current - 3), (cx + rx + 2, cy + 2)]
                fill_polygon(pts, 0)
            px = 2 if side == 'L' else -2
            draw_squircle_pupil(cx + px, cy + 3, 7, 5)
            draw_brow(cx, cy - 1, ry - 2, 0.9)
            
            tear_dy = int((t * 0.02) % 24)
            tx = cx - rx + 5 if side == 'L' else cx + rx - 5
            ty = cy + 2 + tear_dy
            if ty < 62:
                fill_circle(tx, ty, 2, 1)
                oled.pixel(tx, ty - 2, 1)
        elif estado == 'enojado':
            shake = random.randint(-1, 1) if t % 2 == 0 else 0
            fill_superellipse(cx + shake, cy, rx, ry_current)
            if side == 'L':
                pts = [(cx + rx + 2, cy - ry_current - 2), (cx - 3, cy - ry_current - 2), (cx + rx + 2, cy)]
                fill_polygon(pts, 0)
            else:
                pts = [(cx - rx - 2, cy - ry_current - 2), (cx + 3, cy - ry_current - 2), (cx - rx - 2, cy)]
                fill_polygon(pts, 0)
            draw_squircle_pupil(cx + shake, cy + 1)
            draw_brow(cx + shake, cy, ry, -0.8, offset_y=-2)
        elif estado == 'sospechando':
            look_x = int(math.sin(t * 0.002) * 6)
            fill_superellipse(cx, cy + 3, rx, ry_current - 8)
            draw_squircle_pupil(cx + look_x + saccade_dx, cy + 3 + saccade_dy, 8, 5)
            draw_brow(cx, cy + 3, ry - 8, 0, offset_y=-2)
        elif estado == 'travieso':
            dy_bounce = int(abs(math.sin(t * 0.005)) * 4)
            if side == 'L':
                fill_superellipse(cx, cy - dy_bounce, rx, ry_current)
                pts = [(cx + rx + 2, cy - dy_bounce - ry_current - 2), (cx - 3, cy - dy_bounce - ry_current - 2), (cx + rx + 2, cy - dy_bounce + 1)]
                fill_polygon(pts, 0)
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
            dy_breath = int(math.sin(t * 0.002) * 3.0)
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
            fill_superellipse(cx, cy, rx, ry_current)
            draw_squircle_pupil(cx + saccade_dx, cy + saccade_dy)
            scan_y = cy - ry_current + int(((math.sin(t * 0.005) + 1.0)/2.0) * (ry_current * 2))
            if cy - ry_current <= scan_y <= cy + ry_current:
                oled.hline(cx - rx - 2, scan_y, rx * 2 + 5, 0)
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
                oled.fill_rect(cx - rx, cy - 3, rx * 2, 7, 1)
            draw_brow(cx + random.randint(-2, 2), cy, ry, random.uniform(-0.5, 0.5))
        elif estado == 'siguiendo':
            dx = int(math.cos(t * 0.002) * 7)
            dy = int(math.sin(t * 0.002) * 5)
            fill_superellipse(cx, cy, rx, ry_current)
            draw_squircle_pupil(cx + dx + saccade_dx, cy + dy + saccade_dy)
            draw_brow(cx, cy, ry, 0)
        elif estado == 'amor':
            pulse = math.sin(t * 0.006) * 2.0
            fill_heart(cx, cy, int(16 + pulse))
            draw_arch_brow(cx, cy, 12, offset_y=-6, peak_height=4)

    draw_eye(LX, LY, base_rx, base_ry, 'L')
    draw_eye(RX, RY, base_rx, base_ry, 'R')

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

def run_demo():
    """Ejecuta el demo de visualización infinita."""
    while True:
        # 1. Ciclar de forma ordenada por cada emoción
        for e in ESTADOS:
            show_emotion_demo(e)
        
        # 2. Transiciones aleatorias rápidas (5 ciclos)
        for _ in range(5):
            e = random.choice(ESTADOS)
            idx = ESTADOS.index(e) + 1
            print("Transición aleatoria - Gesto %d/%d: %s" % (idx, len(ESTADOS), e.upper()))
            start_time = _get_t()
            while _get_t() - start_time < 2000:
                render(e)
                oled.show()
                try:
                    time.sleep_ms(50)
                except AttributeError:
                    time.sleep(0.05)

if __name__ == '__main__':
    run_demo()
