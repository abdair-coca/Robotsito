# sh1106.py — Driver MicroPython para OLED SH1106 128x64 por I2C.
#
# El SH1106 es casi compatible con el SSD1306 pero NO igual:
#   - El SH1106 tiene 132 columnas (la pantalla muestra 128 centradas):
#     hay un offset de 2 px al inicio de cada página.
#   - No soporta direccionamiento horizontal/vertical: hay que setear
#     página + columna antes de cada fila al refrescar.
#
# La clase SH1106 hereda de framebuf.FrameBuffer (formato MONO_VLSB),
# así que automáticamente expone:
#   fill, pixel, hline, vline, line, rect, fill_rect, text, blit, scroll.
# El único método a implementar es show() que vuelca el buffer al chip.

import framebuf
import time
from micropython import const


# ── Comandos SH1106 ───────────────────────────────────────────────
_CMD_DISP_OFF        = const(0xAE)
_CMD_DISP_ON         = const(0xAF)
_CMD_CONTRAST        = const(0x81)
_CMD_NORM_DISP       = const(0xA6)
_CMD_INV_DISP        = const(0xA7)
_CMD_SET_PAGE        = const(0xB0)   # | page (0..7)
_CMD_SET_COL_LO      = const(0x00)   # | low nibble of col
_CMD_SET_COL_HI      = const(0x10)   # | high nibble of col
_CMD_START_LINE      = const(0x40)   # | start line
_CMD_SEG_REMAP       = const(0xA1)   # 0xA0 = normal, 0xA1 = invertido
_CMD_COM_OUT_REV     = const(0xC8)   # 0xC0 normal, 0xC8 invertido
_CMD_MUX_RATIO       = const(0xA8)
_CMD_DISP_OFFSET     = const(0xD3)
_CMD_CLK_DIV         = const(0xD5)
_CMD_PRECHARGE       = const(0xD9)
_CMD_COM_PINS        = const(0xDA)
_CMD_VCOM_DESEL      = const(0xDB)
_CMD_CHARGE_PUMP     = const(0xAD)   # SH1106 usa 0xAD,0x8B (no el 0x8D del SSD1306)

# Offset de 2 px que tiene el SH1106 (sus columnas internas van 0..131,
# pero la pantalla solo muestra 2..129).
_COL_OFFSET = const(2)


class SH1106(framebuf.FrameBuffer):
    """Clase base — no conectar directamente. Usa SH1106_I2C abajo."""

    def __init__(self, width, height):
        self.width  = width
        self.height = height
        self.pages  = height // 8
        # buffer principal en RAM (1 byte = 8 px verticales)
        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        # Buffer pre-asignado para el envío por página (1 byte prefijo + datos)
        self._page_buf = bytearray(self.width + 1)
        self._page_buf[0] = 0x40   # control byte: Co=0, D/C#=1 -> datos
        self.init_display()

    # Subclases (SH1106_I2C) implementan estos dos métodos.
    def write_cmd(self, cmd):  raise NotImplementedError
    def write_data(self, buf): raise NotImplementedError

    def init_display(self):
        for cmd in (
            _CMD_DISP_OFF,
            _CMD_CLK_DIV,        0x80,
            _CMD_MUX_RATIO,      self.height - 1,
            _CMD_DISP_OFFSET,    0x00,
            _CMD_START_LINE | 0x00,
            _CMD_CHARGE_PUMP,    0x8B,    # bomba de carga interna ON
            _CMD_SEG_REMAP,                # mirror horizontal
            _CMD_COM_OUT_REV,              # mirror vertical
            _CMD_COM_PINS,       0x12,
            _CMD_CONTRAST,       0xFF,
            _CMD_PRECHARGE,      0x1F,
            _CMD_VCOM_DESEL,     0x40,
            _CMD_NORM_DISP,
            _CMD_DISP_ON,
        ):
            self.write_cmd(cmd)
        self.fill(0)
        self.show()

    # ── controles ────────────────────────────────────────────────
    def poweroff(self):       self.write_cmd(_CMD_DISP_OFF)
    def poweron(self):        self.write_cmd(_CMD_DISP_ON)
    def contrast(self, v):
        self.write_cmd(_CMD_CONTRAST)
        self.write_cmd(v & 0xFF)
    def invert(self, on):
        self.write_cmd(_CMD_INV_DISP if on else _CMD_NORM_DISP)

    # ── volcar buffer al chip ────────────────────────────────────
    def show(self):
        """Manda las 8 páginas al SH1106. Usa _page_buf preasignado:
        cero asignaciones de memoria por frame (después de la primera)."""
        buf = self.buffer
        page_buf = self._page_buf
        width = self.width
        for page in range(self.pages):
            self.write_cmd(_CMD_SET_PAGE | page)
            self.write_cmd(_CMD_SET_COL_LO | (_COL_OFFSET & 0x0F))
            self.write_cmd(_CMD_SET_COL_HI | ((_COL_OFFSET >> 4) & 0x0F))
            # copiar fila al buffer prefijado (slice asignación = in-place)
            page_buf[1:] = buf[page * width : (page + 1) * width]
            self.write_data(page_buf)


class SH1106_I2C(SH1106):
    """SH1106 sobre I2C/SoftI2C. Hace reset por hardware si se pasa el pin."""

    def __init__(self, width, height, i2c, reset=None, addr=0x3C):
        self.i2c  = i2c
        self.addr = addr
        # buffer de 2 bytes pre-asignado para comandos (Co=1, D/C#=0 + cmd)
        self._cmd_buf = bytearray(2)
        self._cmd_buf[0] = 0x80

        # Reset por hardware (opcional pero recomendado para SH1106)
        if reset is not None:
            from machine import Pin
            reset.init(Pin.OUT, value=1)
            time.sleep_ms(1)
            reset.value(0)
            time.sleep_ms(10)
            reset.value(1)
            time.sleep_ms(10)

        super().__init__(width, height)

    def write_cmd(self, cmd):
        self._cmd_buf[1] = cmd & 0xFF
        self.i2c.writeto(self.addr, self._cmd_buf)

    def write_data(self, buf):
        # buf ya viene con el prefijo 0x40 al inicio
        self.i2c.writeto(self.addr, buf)
