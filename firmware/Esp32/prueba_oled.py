# prueba_oled.py — Verificación standalone del OLED SH1106 128x64.
# Ejecutar desde Thonny con F5 antes de flashear main.py para asegurar
# que el hardware está bien cableado.
#
# Hace 4 pruebas en orden:
#   1. Escanea el bus I2C y lista las direcciones encontradas.
#   2. Llena la pantalla en blanco.
#   3. Texto y rectángulos (chequea framebuf básico).
#   4. Las 7 expresiones de oled_ojos.py más animaciones.
#
# No importa main.py — funciona aunque audio/servos aún no estén configurados.

import time
import math
from machine import Pin, SoftI2C


# ── 1. Escaneo del bus I2C ──────────────────────────────────────
print('=' * 50)
print('Prueba OLED SH1106 — 128x64')
print('=' * 50)

print('\n[1] Escaneando bus I2C en GPIO22(SCL) / GPIO21(SDA)...')
i2c_scan = SoftI2C(scl=Pin(22), sda=Pin(21), freq=400000)
dispositivos = i2c_scan.scan()
print('    Dispositivos encontrados:', [hex(d) for d in dispositivos])

# Elegir dirección: 0x3C por defecto; 0x3D si solo aparece esa
ADDR = 0x3C
if 0x3C not in dispositivos and 0x3D in dispositivos:
    ADDR = 0x3D
    print('    -> Usando 0x3D (jumper alternativo)')
elif 0x3C in dispositivos:
    print('    -> Usando 0x3C (estándar)')
else:
    print('    !! OLED no detectado. Revisar cableado:')
    print('       SCL -> GPIO22, SDA -> GPIO21, RST -> GPIO16,')
    print('       VCC -> 3V3, GND -> GND.')
    raise SystemExit


# ── 2. Importamos oled_ojos (esto inicializa el display) ────────
# Lo importamos DESPUÉS del scan para no abrir dos veces el bus.
print('\n[2] Inicializando display vía oled_ojos.py ...')
import oled_ojos
disp = oled_ojos.disp
print('    OK')


# ── 3. Tests de framebuf ────────────────────────────────────────
print('\n[3] Test pantalla blanca (1.5 s)')
disp.fill(1)
disp.show()
time.sleep(1.5)

print('\n[4] Test texto (2 s)')
disp.fill(0)
disp.text('Creeper OLED', 5, 4, 1)
disp.text('SH1106 I2C OK', 5, 18, 1)
disp.text('128x64 px', 5, 32, 1)
disp.text('addr: ' + hex(ADDR), 5, 46, 1)
disp.show()
time.sleep(2)

print('[5] Test rectangulos (2 s)')
disp.fill(0)
disp.rect(0, 0, 128, 64, 1)            # marco exterior
disp.rect(8, 8, 112, 48, 1)            # marco interior
disp.fill_rect(48, 24, 32, 16, 1)      # bloque relleno
# unas líneas diagonales con el método line
disp.line(0, 0, 127, 63, 1)
disp.line(127, 0, 0, 63, 1)
disp.show()
time.sleep(2)


# ── 4. Expresiones ──────────────────────────────────────────────
print('\n[6] Expresiones de Creeper:')

expresiones_estaticas = (
    ('normal',   oled_ojos.ojos_normal),
    ('abiertos', oled_ojos.ojos_abiertos),
    ('pensando', oled_ojos.ojos_pensando),
    ('feliz',    oled_ojos.ojos_feliz),
    ('curioso',  oled_ojos.ojos_curioso),
)
for nombre, fn in expresiones_estaticas:
    print('    ->', nombre)
    fn()
    time.sleep_ms(1300)

print('    -> parpadear')
oled_ojos.ojos_normal()
time.sleep_ms(400)
oled_ojos.parpadear()
time.sleep_ms(400)

print('    -> hablando (animación)')
for i in range(20):
    oled_ojos.ojos_hablando(i)
    time.sleep_ms(110)

print('    -> siguiendo (pupilas en círculo)')
for ang in range(0, 360, 12):
    rad = ang * 0.01745329
    oled_ojos.ojos_siguiendo(math.cos(rad), math.sin(rad))
    time.sleep_ms(50)

# Vuelta a reposo
oled_ojos.ojos_normal()

print('\n[7] Prueba completa. OLED listo para integrarse con main.py')
