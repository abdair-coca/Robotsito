# MicroPython SH1106 OLED driver, I2C interface
# Inherits from framebuf.FrameBuffer to automatically support all drawing methods:
# fill, pixel, hline, vline, line, rect, fill_rect, text, blit, scroll.

import framebuf
from micropython import const

_CMD_DISP_OFF        = const(0xAE)
_CMD_DISP_ON         = const(0xAF)
_CMD_CONTRAST        = const(0x81)
_CMD_NORM_DISP       = const(0xA6)
_CMD_INV_DISP        = const(0xA7)
_CMD_SET_PAGE        = const(0xB0)
_CMD_SET_COL_LO      = const(0x00)
_CMD_SET_COL_HI      = const(0x10)
_CMD_START_LINE      = const(0x40)
_CMD_SEG_REMAP       = const(0xA1) # Mirror horizontal (0xA1 o 0xA0)
_CMD_COM_OUT_REV     = const(0xC8) # Mirror vertical (0xC8 o 0xC0)
_CMD_MUX_RATIO       = const(0xA8)
_CMD_DISP_OFFSET     = const(0xD3)
_CMD_CLK_DIV         = const(0xD5)
_CMD_PRECHARGE       = const(0xD9)
_CMD_COM_PINS        = const(0xDA)
_CMD_VCOM_DESEL      = const(0xDB)
_CMD_CHARGE_PUMP     = const(0xAD)

_COL_OFFSET = const(2) # El offset típico de 2 píxeles de la pantalla SH1106

class SH1106(framebuf.FrameBuffer):
    def __init__(self, width, height, i2c, addr=0x3C, external_vcc=False):
        self.width = width
        self.height = height
        self.i2c = i2c
        self.addr = addr
        self.external_vcc = external_vcc
        self.pages = self.height // 8
        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self._page_buf = bytearray(self.width + 1)
        self._page_buf[0] = 0x40 # Control byte: datos
        self._cmd_buf = bytearray(2)
        self._cmd_buf[0] = 0x80 # Control byte: comando
        self.init_display()

    def write_cmd(self, cmd):
        self._cmd_buf[1] = cmd & 0xFF
        self.i2c.writeto(self.addr, self._cmd_buf)

    def write_data(self, buf):
        self.i2c.writeto(self.addr, buf)

    def init_display(self):
        for cmd in (
            _CMD_DISP_OFF,
            _CMD_CLK_DIV,        0x80,
            _CMD_MUX_RATIO,      self.height - 1,
            _CMD_DISP_OFFSET,    0x00,
            _CMD_START_LINE | 0x00,
            _CMD_CHARGE_PUMP,    0x8B, # Bomba de carga interna ON (SH1106 usa 0x8B)
            _CMD_SEG_REMAP,            # Remap de columnas
            _CMD_COM_OUT_REV,          # COM output scan direction
            _CMD_COM_PINS,       0x12 if self.height == 64 else 0x02,
            _CMD_CONTRAST,       0xFF,
            _CMD_PRECHARGE,      0x22 if self.external_vcc else 0xF1,
            _CMD_VCOM_DESEL,     0x35, # Deselección VCOM
            _CMD_NORM_DISP,
            _CMD_DISP_ON,
        ):
            self.write_cmd(cmd)
        self.fill(0)
        self.show()

    def poweroff(self):
        self.write_cmd(_CMD_DISP_OFF)

    def poweron(self):
        self.write_cmd(_CMD_DISP_ON)

    def contrast(self, v):
        self.write_cmd(_CMD_CONTRAST)
        self.write_cmd(v & 0xFF)

    def invert(self, on):
        self.write_cmd(_CMD_INV_DISP if on else _CMD_NORM_DISP)

    def show(self):
        buf = self.buffer
        page_buf = self._page_buf
        width = self.width
        for page in range(self.pages):
            self.write_cmd(_CMD_SET_PAGE | page)
            self.write_cmd(_CMD_SET_COL_LO | (_COL_OFFSET & 0x0F))
            self.write_cmd(_CMD_SET_COL_HI | ((_COL_OFFSET >> 4) & 0x0F))
            page_buf[1:] = buf[page * width : (page + 1) * width]
            self.write_data(page_buf)

# Alias para compatibilidad con código que use SH1106_I2C o SH1106
SH1106_I2C = SH1106