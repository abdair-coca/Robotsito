"""
test_motor.py — Manejo manual de los motores DC por teclado (Fase 2).

Usa SerialManager (resuelve WiFi 5007 → USB COM3) y manda el comando M:<izq>,<der>.
Reenvía el comando actual cada ~150 ms para alimentar el watchdog del firmware
(que para los motores a los 400 ms sin comando).

Controles:
  W = adelante   S = atrás   A = giro izq   D = giro der   ESPACIO = parar   Q = salir

⚠️ Para la primera prueba, poné las ruedas EN EL AIRE.

Ejecutar (Windows):
  python tests/test_motor.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import PUERTO_SERIAL, BAUD_RATE
from serial_manager import SerialManager

try:
    import msvcrt   # lectura de teclas no bloqueante (Windows)
except ImportError:
    msvcrt = None


def leer_tecla():
    if msvcrt and msvcrt.kbhit():
        try:
            return msvcrt.getwch().lower()
        except Exception:
            return None
    return None


def main() -> None:
    print('═' * 50)
    print('  TEST MOTORES DC — manejo por teclado')
    print('═' * 50)
    print('  W=adelante  S=atrás  A=izq  D=der  ESPACIO=parar')
    print('  +/- = subir/bajar velocidad   Q=salir')
    print('  ⚠️  Ruedas EN EL AIRE para la primera prueba.\n')

    if msvcrt is None:
        print('Este test usa msvcrt (Windows). En otro SO, adaptá la lectura de teclas.')
        return

    sm = SerialManager(PUERTO_SERIAL, BAUD_RATE)
    vel = 70           # velocidad actual (0-100, PWM)
    izq, der = 0, 0
    direccion = (0, 0)  # sentido actual (signos)
    print('velocidad: %d' % vel)
    try:
        while True:
            k = leer_tecla()
            if k == 'q':
                break
            elif k == 'w':
                direccion = (1, 1);   print('adelante')
            elif k == 's':
                direccion = (-1, -1); print('atrás')
            elif k == 'a':
                direccion = (-1, 1);  print('izquierda')
            elif k == 'd':
                direccion = (1, -1);  print('derecha')
            elif k == ' ':
                direccion = (0, 0);   print('parar')
            elif k == '+':
                vel = min(100, vel + 10); print('velocidad: %d' % vel)
            elif k == '-':
                vel = max(0,   vel - 10); print('velocidad: %d' % vel)
            izq, der = direccion[0] * vel, direccion[1] * vel

            # Reenvío continuo: alimenta el watchdog del firmware (400 ms).
            sm.cmd_motor(izq, der)
            time.sleep(0.15)
    finally:
        sm.cmd_motor(0, 0)
        time.sleep(0.2)
        sm.cerrar()
        print('\nMotores parados. Fin del test.')


if __name__ == '__main__':
    main()
