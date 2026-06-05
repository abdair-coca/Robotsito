from machine import Pin, I2C
from sh1106 import SH1106

i2c = I2C(
    0,
    scl=Pin(22),
    sda=Pin(21),
    freq=100000
)

oled = SH1106(128, 64, i2c)

oled.fill(0)
oled.text("Hola Abdair!", 0, 0)
oled.text("SH1106 TEST", 0, 20)
oled.text("ESP32 OK", 0, 40)
oled.show()
