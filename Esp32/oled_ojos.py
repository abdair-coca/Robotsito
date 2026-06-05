# oled_ojos.py — Expresiones animadas para Creeper en OLED SH1106 128x64.
#
# Cada expresión se dibuja con ELIPSES matemáticas (ecuación x²/a² + y²/b² = 1)
# rellenadas scanline por scanline. Sin librerías de gráficos externas, solo
# math + framebuf.
#
# API pública:
#   ojos_normal()
#   ojos_abiertos()
#   ojos_pensando()
#   ojos_hablando(frame)
#   ojos_feliz()
#   ojos_curioso()
#   ojos_siguiendo(dx, dy)
#   parpadear()
#
# Demo: `python oled_ojos.py` en el ESP32 cicla por todas las expresiones.

import math
import time
from machine import Pin, SoftI2C
from sh1106 import SH1106_I2C


# ── Inicialización del display ─────────────────────────────────
# (al importar este módulo, el OLED queda listo en `disp`)
_i2c = SoftI2C(scl=Pin(22), sda=Pin(21), freq=400000)
disp = SH1106_I2C(128, 64, _i2c, Pin(16), addr=0x3C)


# ── Geometría base ─────────────────────────────────────────────
OJO_IZQ_X = 32      # centro horizontal del ojo izquierdo
OJO_DER_X = 96      # centro horizontal del ojo derecho
OJO_Y     = 32      # centro vertical de ambos ojos

OJO_RX    = 14      # radio horizontal — ancho ≈ 28 px
OJO_RY    = 11      # radio vertical   — alto  ≈ 22 px

PUPILA_R          = 4    # radio de la pupila negra interior
PUPILA_MAX_DESP   = 5    # tope (px) de desplazamiento en ojos_siguiendo


# ══════════════════════════════════════════════════════════════════
# HELPERS DE DIBUJO — todos operan directamente sobre `disp`
# ══════════════════════════════════════════════════════════════════

def _fill_ellipse(cx, cy, rx, ry, color):
    """Elipse rellena usando x²/rx² + y²/ry² = 1, scanline por scanline.
    Para cada fila y, calcula x_max = rx·sqrt(1 - y²/ry²) y dibuja
    una hline horizontal centrada. Sin alocaciones de listas/strings."""
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
    """Solo la mitad inferior de una elipse (para los ojos felices ^^)."""
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


def _ojo(cx, cy, rx, ry, pup_dx=0, pup_dy=0):
    """Dibuja un ojo completo: globo blanco + pupila negra + brillo blanco.
    pup_dx/dy: desplazamiento de la pupila respecto al centro del ojo."""
    # globo ocular (blanco)
    _fill_ellipse(cx, cy, rx, ry, 1)
    # pupila (negra)
    pup_x = cx + pup_dx
    pup_y = cy + pup_dy
    _fill_ellipse(pup_x, pup_y, PUPILA_R, PUPILA_R, 0)
    # brillo: 2 px blancos en la esquina superior-izquierda de la pupila
    disp.pixel(pup_x - 1, pup_y - 1, 1)
    disp.pixel(pup_x - 2, pup_y - 2, 1)


# ══════════════════════════════════════════════════════════════════
# EXPRESIONES
# ══════════════════════════════════════════════════════════════════

def ojos_normal():
    """Reposo: ojos mirando al frente, tamaño normal."""
    disp.fill(0)
    _ojo(OJO_IZQ_X, OJO_Y, OJO_RX, OJO_RY)
    _ojo(OJO_DER_X, OJO_Y, OJO_RX, OJO_RY)
    disp.show()


def ojos_abiertos():
    """Escuchando: ojos más grandes y abiertos."""
    disp.fill(0)
    _ojo(OJO_IZQ_X, OJO_Y, OJO_RX + 2, OJO_RY + 3)
    _ojo(OJO_DER_X, OJO_Y, OJO_RX + 2, OJO_RY + 3)
    disp.show()


def ojos_pensando():
    """Pensando: ojo izq entrecerrado (línea fina), ojo der mira arriba."""
    disp.fill(0)
    # izquierdo entrecerrado — línea horizontal gruesa
    _fill_ellipse(OJO_IZQ_X, OJO_Y, OJO_RX, 3, 1)
    # derecho normal con pupila apuntando arriba
    _ojo(OJO_DER_X, OJO_Y, OJO_RX, OJO_RY, 0, -4)
    disp.show()


def ojos_hablando(frame):
    """Boca/ojos en movimiento al hablar: parpadeo alterno (4 fases)."""
    disp.fill(0)
    fase = frame & 3   # 0,1,2,3
    if fase == 0 or fase == 2:
        # ambos abiertos
        _ojo(OJO_IZQ_X, OJO_Y, OJO_RX, OJO_RY)
        _ojo(OJO_DER_X, OJO_Y, OJO_RX, OJO_RY)
    elif fase == 1:
        # izq abierto, der entrecerrado
        _ojo(OJO_IZQ_X, OJO_Y, OJO_RX, OJO_RY)
        _fill_ellipse(OJO_DER_X, OJO_Y, OJO_RX, 5, 1)
    else:   # fase == 3
        _fill_ellipse(OJO_IZQ_X, OJO_Y, OJO_RX, 5, 1)
        _ojo(OJO_DER_X, OJO_Y, OJO_RX, OJO_RY)
    disp.show()


def ojos_feliz():
    """Ojos curvados hacia arriba (^_^): solo la mitad inferior."""
    disp.fill(0)
    _fill_ellipse_inferior(OJO_IZQ_X, OJO_Y - 2, OJO_RX, OJO_RY, 1)
    _fill_ellipse_inferior(OJO_DER_X, OJO_Y - 2, OJO_RX, OJO_RY, 1)
    disp.show()


def ojos_curioso():
    """Curioso: ojo izq agrandado, ojo der entrecerrado."""
    disp.fill(0)
    _ojo(OJO_IZQ_X, OJO_Y, OJO_RX + 4, OJO_RY + 4)
    _ojo(OJO_DER_X, OJO_Y, OJO_RX - 2, OJO_RY - 3)
    disp.show()


def ojos_siguiendo(dx, dy):
    """Pupilas siguen el rostro: dx, dy normalizados a [-1, 1].
    Desplazamiento máximo de pupila: PUPILA_MAX_DESP px."""
    disp.fill(0)
    # clamp manual (sin allocs)
    if dx >  1.0: dx =  1.0
    elif dx < -1.0: dx = -1.0
    if dy >  1.0: dy =  1.0
    elif dy < -1.0: dy = -1.0
    px = int(dx * PUPILA_MAX_DESP)
    py = int(dy * PUPILA_MAX_DESP)
    _ojo(OJO_IZQ_X, OJO_Y, OJO_RX, OJO_RY, px, py)
    _ojo(OJO_DER_X, OJO_Y, OJO_RX, OJO_RY, px, py)
    disp.show()


# Fases del parpadeo: altura vertical de la elipse en cada cuadro
# (de abierto -> cerrado -> abierto). Tupla const => sin allocs por llamada.
_PARPADEO_FASES = (OJO_RY, 8, 4, 2, 4, 8, OJO_RY)

def parpadear():
    """Cierre y apertura suave de ambos ojos. ~140 ms total."""
    for ry in _PARPADEO_FASES:
        disp.fill(0)
        _fill_ellipse(OJO_IZQ_X, OJO_Y, OJO_RX, ry, 1)
        _fill_ellipse(OJO_DER_X, OJO_Y, OJO_RX, ry, 1)
        # pupila + brillo solo cuando el ojo está suficientemente abierto
        if ry >= OJO_RY - 2:
            _fill_ellipse(OJO_IZQ_X, OJO_Y, PUPILA_R, PUPILA_R, 0)
            _fill_ellipse(OJO_DER_X, OJO_Y, PUPILA_R, PUPILA_R, 0)
            disp.pixel(OJO_IZQ_X - 1, OJO_Y - 1, 1)
            disp.pixel(OJO_DER_X - 1, OJO_Y - 1, 1)
        disp.show()
        time.sleep_ms(20)


# ══════════════════════════════════════════════════════════════════
# DEMO — recorre todas las expresiones en bucle
# ══════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print('Demo oled_ojos — Ctrl+C para salir')
    try:
        while True:
            print(' normal');    ojos_normal();    time.sleep_ms(1500)
            print(' parpadea');  parpadear();      time.sleep_ms(300)
            print(' abiertos');  ojos_abiertos();  time.sleep_ms(1500)
            print(' pensando');  ojos_pensando();  time.sleep_ms(1500)
            print(' hablando')
            for i in range(16):
                ojos_hablando(i)
                time.sleep_ms(120)
            print(' feliz');     ojos_feliz();     time.sleep_ms(1500)
            print(' curioso');   ojos_curioso();   time.sleep_ms(1500)
            print(' siguiendo (circular)')
            for ang in range(0, 360, 12):
                rad = ang * 0.01745329   # grados -> rad
                ojos_siguiendo(math.cos(rad), math.sin(rad))
                time.sleep_ms(45)
    except KeyboardInterrupt:
        ojos_normal()
        print('demo terminada')
