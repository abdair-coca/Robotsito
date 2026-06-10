# oled_ojos_prove.py — Sistema de Ojos Emocionales para Bob (Creeper)
# Diseño elegido: "Futuristas" (squircles / superelipses).
# Optimizado para pantalla OLED SH1106 de 128x64 sobre MicroPython/ESP32.

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
# RENDERIZADO DEL ROSTRO COMPLETO (OJOS + BOCA)
# ============================================================

def render(e=None):
    """Dibuja un frame del rostro animado según el estado."""
    global estado
    if e is not None:
        estado = e
        
    oled.fill(0)
    t = _get_t()
    
    LX, LY, RX, RY = 32, 30, 96, 30
    base_rx, base_ry = 16, 13
    
    def draw_eye(cx, cy, rx, ry, side):
        if estado == 'neutral':
            dx = int(math.sin(t * 0.001) * 1.2)
            dy = int(math.cos(t * 0.0015) * 0.7)
            fill_superellipse(cx + dx, cy + dy, rx, ry)
        elif estado == 'feliz':
            dy_bounce = int(abs(math.sin(t * 0.004)) * 2)
            fill_superellipse(cx, cy - dy_bounce, rx, ry)
            fill_ellipse(cx, cy - dy_bounce + 7, rx + 4, ry - 2, 0)
        elif estado == 'muy_feliz':
            dy_bounce = int(abs(math.sin(t * 0.006)) * 4)
            fill_superellipse(cx, cy - dy_bounce, rx, ry - 1)
            fill_ellipse(cx, cy - dy_bounce + 5, rx + 4, ry - 3, 0)
            by = cy - dy_bounce + ry + 2
            if by < 62:
                for bx in (cx - 10, cx - 6, cx + 6, cx + 10):
                    oled.pixel(bx, by, 1)
                    oled.pixel(bx + 1, by - 1, 1)
        elif estado == 'escuchando':
            pulse = math.sin(t * 0.005) * 0.8
            fill_superellipse(cx, cy - 2, int(rx + pulse), ry + 1)
        elif estado == 'pensando':
            fill_superellipse(cx, cy, rx, ry)
            px, py = 4, -3
            fill_circle(cx + px, cy + py, 4, 0)
            if side == 'L':
                oled.fill_rect(cx - rx - 1, cy - ry - 1, 2*rx + 3, ry // 2 + 1, 0)
            else:
                oled.fill_rect(cx - rx - 1, cy - ry - 1, 2*rx + 3, ry // 3, 0)
        elif estado == 'curioso':
            if side == 'L':
                h_wobble = int(math.sin(t * 0.003) * 1.5)
                fill_superellipse(cx, cy - 2, rx, ry + 2 + h_wobble)
            else:
                h_wobble = int(math.cos(t * 0.003) * 1.5)
                fill_superellipse(cx, cy + 1, rx - 1, ry - 2 + h_wobble)
        elif estado == 'sorprendido':
            shake_x = random.randint(-1, 1) if t % 2 == 0 else 0
            shake_y = random.randint(-1, 1) if t % 3 == 0 else 0
            fill_superellipse(cx + shake_x, cy + shake_y - 2, rx - 2, ry + 3)
            fill_circle(cx + shake_x, cy + shake_y - 2, 4, 0)
        elif estado == 'confundido':
            fill_superellipse(cx, cy, rx - 1, ry - 1)
            if side == 'L':
                pts = [(cx - rx, cy - ry), (cx + 2, cy - ry), (cx - rx, cy + 1)]
                fill_polygon(pts, 0)
            else:
                pts_r = [(cx - rx, cy - ry), (cx, cy - ry), (cx - rx, cy + 2)]
                fill_polygon(pts_r, 0)
        elif estado == 'triste':
            fill_superellipse(cx, cy, rx, ry)
            if side == 'L':
                pts = [(cx - rx - 2, cy - ry - 2), (cx, cy - ry - 2), (cx - rx - 2, cy)]
                fill_polygon(pts, 0)
            else:
                pts = [(cx + rx + 2, cy - ry - 2), (cx, cy - ry - 2), (cx + rx + 2, cy)]
                fill_polygon(pts, 0)
            if side == 'L':
                tear_dy = int((t * 0.015) % 20)
                tx, ty = cx - rx + 2, cy + 2 + tear_dy
                if ty < 60:
                    fill_circle(tx, ty, 2, 1)
                    oled.pixel(tx, ty - 2, 1)
        elif estado == 'muy_triste':
            fill_superellipse(cx, cy - 1, rx, ry - 1)
            if side == 'L':
                pts = [(cx - rx - 2, cy - ry - 3), (cx + 2, cy - ry - 3), (cx - rx - 2, cy + 1)]
                fill_polygon(pts, 0)
            else:
                pts = [(cx + rx + 2, cy - ry - 3), (cx - 2, cy - ry - 3), (cx + rx + 2, cy + 1)]
                fill_polygon(pts, 0)
            tear_dy = int((t * 0.02) % 22)
            tx = cx - rx + 4 if side == 'L' else cx + rx - 4
            ty = cy + 2 + tear_dy
            if ty < 62:
                fill_circle(tx, ty, 2, 1)
                oled.pixel(tx, ty - 2, 1)
        elif estado == 'enojado':
            shake = random.randint(-1, 1) if t % 2 == 0 else 0
            fill_superellipse(cx + shake, cy, rx, ry)
            if side == 'L':
                pts = [(cx + rx + 2, cy - ry - 2), (cx - 2, cy - ry - 2), (cx + rx + 2, cy - 2)]
                fill_polygon(pts, 0)
            else:
                pts = [(cx - rx - 2, cy - ry - 2), (cx + 2, cy - ry - 2), (cx - rx - 2, cy - 2)]
                fill_polygon(pts, 0)
        elif estado == 'sospechando':
            look_x = int(math.sin(t * 0.002) * 5)
            fill_superellipse(cx, cy + 2, rx, ry - 6)
            fill_circle(cx + look_x, cy + 2, 3, 0)
        elif estado == 'travieso':
            dy_bounce = int(abs(math.sin(t * 0.005)) * 3)
            if side == 'L':
                fill_superellipse(cx, cy - dy_bounce, rx, ry)
                pts = [(cx + rx + 2, cy - dy_bounce - ry - 2), (cx - 2, cy - dy_bounce - ry - 2), (cx + rx + 2, cy - dy_bounce)]
                fill_polygon(pts, 0)
            else:
                bx = cx
                by = cy - dy_bounce + 2
                oled.line(bx - 12, by, bx - 6, by + 3, 1)
                oled.line(bx - 6, by + 3, bx + 6, by + 3, 1)
                oled.line(bx + 6, by + 3, bx + 12, by, 1)
                oled.line(bx - 12, by + 1, bx - 6, by + 4, 1)
                oled.line(bx - 6, by + 4, bx + 6, by + 4, 1)
                oled.line(bx + 6, by + 4, bx + 12, by + 1, 1)
        elif estado == 'orgulloso':
            dy_breath = int(math.sin(t * 0.002) * 2.0)
            bx = cx
            by = cy - 3 + dy_breath
            oled.line(bx - 12, by + 3, bx - 6, by, 1)
            oled.line(bx - 6, by, bx + 6, by, 1)
            oled.line(bx + 6, by, bx + 12, by + 3, 1)
            oled.line(bx - 12, by + 4, bx - 6, by + 1, 1)
            oled.line(bx - 6, by + 1, bx + 6, by + 1, 1)
            oled.line(bx + 6, by + 1, bx + 12, by + 4, 1)
        elif estado == 'dormido':
            oled.hline(cx - 13, cy + 3, 27, 1)
            oled.hline(cx - 13, cy + 4, 27, 1)
        elif estado == 'durmiendo':
            dy_breath = int(math.sin(t * 0.0015) * 1.8)
            bx = cx
            by = cy + 2 + dy_breath
            oled.line(bx - 12, by + 3, bx - 6, by, 1)
            oled.line(bx - 6, by, bx + 6, by, 1)
            oled.line(bx + 6, by, bx + 12, by + 3, 1)
            oled.line(bx - 12, by + 4, bx - 6, by + 1, 1)
            oled.line(bx - 6, by + 1, bx + 6, by + 1, 1)
            oled.line(bx + 6, by + 1, bx + 12, by + 4, 1)
            if side == 'R':
                z_state = (t // 80) % 60
                zx = cx + 16 + z_state // 2
                zy = cy - 10 - z_state // 3
                if zy > 2:
                    oled.text('z', zx, zy, 1)
                z_state2 = ((t + 1500) // 80) % 60
                zx2 = cx + 18 + z_state2 // 2
                zy2 = cy - 10 - z_state2 // 3
                if zy2 > 2:
                    oled.text('Z', zx2, zy2, 1)
        elif estado == 'procesando':
            fill_superellipse(cx, cy, rx, ry)
            scan_y = cy - ry + int(((math.sin(t * 0.005) + 1.0)/2.0) * (ry * 2))
            if cy - ry <= scan_y <= cy + ry:
                oled.hline(cx - rx - 2, scan_y, rx * 2 + 5, 0)
        elif estado == 'error':
            glitch_t = t % 1000
            if glitch_t < 800:
                for d in range(-8, 9):
                    oled.pixel(cx + d, cy + d, 1)
                    oled.pixel(cx + d, cy - d, 1)
                    oled.pixel(cx + d + 1, cy + d, 1)
                    oled.pixel(cx + d - 1, cy + d, 1)
            else:
                oled.fill_rect(cx - rx, cy - 2, rx * 2, 5, 1)
        elif estado == 'siguiendo':
            dx = int(math.cos(t * 0.002) * 5)
            dy = int(math.sin(t * 0.002) * 3)
            fill_superellipse(cx, cy, rx, ry)
            fill_circle(cx + dx, cy + dy, 3, 0)
        elif estado == 'amor':
            pulse = math.sin(t * 0.006) * 1.5
            fill_heart(cx, cy, int(12 + pulse))

    draw_eye(LX, LY, base_rx, base_ry, 'L')
    draw_eye(RX, RY, base_rx, base_ry, 'R')
    
    # Boca pequeña interactiva en la parte inferior central
    if estado == 'neutral':
        oled.hline(58, 52, 12, 1)
    elif estado in ('feliz', 'muy_feliz', 'escuchando'):
        oled.line(56, 51, 60, 54, 1)
        oled.line(60, 54, 68, 54, 1)
        oled.line(68, 54, 72, 51, 1)
    elif estado in ('triste', 'muy_triste', 'confundido'):
        oled.line(56, 54, 60, 51, 1)
        oled.line(60, 51, 68, 51, 1)
        oled.line(68, 51, 72, 54, 1)
    elif estado in ('enojado', 'sospechando', 'procesando'):
        oled.hline(58, 53, 12, 1)
    elif estado == 'sorprendido':
        fill_circle(64, 52, 3)
    elif estado in ('travieso', 'guino'):
        fill_circle(64, 52, 2)
        oled.pixel(64, 52, 0)
    elif estado == 'amor':
        oled.line(58, 51, 60, 53, 1)
        oled.line(60, 53, 68, 53, 1)
        oled.line(68, 53, 70, 51, 1)

# ============================================================
# DEMO INTERACTIVA
# ============================================================

def show_emotion_demo(e):
    """Cicla animando una sola emoción por 3.5 segundos."""
    start_time = _get_t()
    while _get_t() - start_time < 3500:
        render(e)
        # Dibujar UI bonita de demo
        oled.rect(0, 53, 128, 11, 1)
        oled.fill_rect(1, 54, 126, 9, 0)
        oled.text(e.upper()[:12], 4, 55, 1)
        oled.text('%d/%d' % (ESTADOS.index(e) + 1, len(ESTADOS)), 90, 55, 1)
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
            start_time = _get_t()
            while _get_t() - start_time < 2000:
                render(e)
                oled.rect(0, 53, 128, 11, 1)
                oled.fill_rect(1, 54, 126, 9, 0)
                oled.text('RAND:%s' % e.upper()[:7], 2, 55, 1)
                oled.show()
                try:
                    time.sleep_ms(50)
                except AttributeError:
                    time.sleep(0.05)

if __name__ == '__main__':
    run_demo()
