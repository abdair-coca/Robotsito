from machine import Pin, I2C
from sh1106 import SH1106
import time
import random

i2c = I2C(
    0,
    scl=Pin(22),
    sda=Pin(21),
    freq=100000
)

oled = SH1106(128, 64, i2c)

def ojos_abiertos():
    oled.fill(0)

    # Ojo izquierdo
    oled.fill_rect(20, 18, 30, 20, 1)

    # Ojo derecho
    oled.fill_rect(78, 18, 30, 20, 1)

    oled.show()

def ojos_cerrados():
    oled.fill(0)

    # Párpados
    oled.fill_rect(20, 28, 30, 3, 1)
    oled.fill_rect(78, 28, 30, 3, 1)

    oled.show()
def feliz():
    oled.fill(0)

    oled.line(20, 30, 50, 20, 1)
    oled.line(78, 20, 108, 30, 1)

    oled.show()

def enojado():
    oled.fill(0)

    oled.line(20, 20, 50, 30, 1)
    oled.line(78, 30, 108, 20, 1)

    oled.show()

def sorprendido():
    oled.fill(0)

    for r in range(8):
        oled.pixel(35+r, 30, 1)

    oled.fill_rect(25, 20, 20, 20, 1)
    oled.fill_rect(83, 20, 20, 20, 1)

    oled.show()
facciones = [ojos_cerrados, feliz, enojado, sorprendido]
while True:
    ojos_abiertos()

    # tiempo aleatorio entre parpadeos
    time.sleep(random.uniform(0.1, 1.7))
    
    
    random.choice(facciones)()
    time.sleep(0.15)

    ojos_abiertos()
