"""
test_servo_tilt.py — Prueba aislada del servo de tilt (GPIO12).

Mueve solo tilt en un barrido; pan se queda fijo en 90.
Los extremos (40 y 140) son los TILT_MIN/TILT_MAX del firmware Esp32/main.py.

Ejecutar:
  cd robot_bob
  python tests/hardware/test_servo_tilt.py
"""

import os
import sys
import time

# Agregar el directorio padre al path para importar robot_bob
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from hardware.serial_manager import SerialManager

PUERTO = 'COM3'
BAUD   = 115200

PAN_FIJO            = 90
POSICIONES_TILT     = [90, 40, 60, 90, 120, 140, 90]
TIEMPO_POR_POSICION = 1.5


def main() -> None:
    print('═' * 60)
    print('  TEST SERVO TILT — solo tilt, pan fijo en 90')
    print('═' * 60)

    mgr = SerialManager(PUERTO, BAUD)
    time.sleep(2.5)  # esperar a que el ESP32 esté listo

    try:
        for tilt in POSICIONES_TILT:
            print(f'  → Pan={PAN_FIJO} Tilt={tilt}')
            mgr.cmd_servo(PAN_FIJO, tilt)
            time.sleep(TIEMPO_POR_POSICION)
        print('  ✓ Si el servo de tilt se movió entre 40 y 140, OK')

    finally:
        mgr.cmd_servo(90, 90)
        time.sleep(1.0)
        mgr.cerrar()
        print('Listo.')


if __name__ == '__main__':
    main()
