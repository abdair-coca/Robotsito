"""
test_1_serial.py — Prueba aislada del SerialManager.

Qué prueba:
  1. Que COM3 se abre correctamente
  2. Que el OLED responde a comandos ESTADO
  3. Que los servos pan/tilt se mueven
  4. Que los comandos SIGUIENDO funcionan
  5. Que la cola priorizada respeta los throttles

Ejecutar:
  cd robot_bob
  python tests/test_1_serial.py
"""

import os
import sys
import time

# Agregar el directorio padre al path para importar robot_bob
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from serial_manager import SerialManager

PUERTO = 'COM3'
BAUD   = 115200


def banner(msg: str) -> None:
    print('\n' + '=' * 60)
    print(f'  {msg}')
    print('=' * 60)


def prueba_estados(mgr: SerialManager) -> None:
    banner('Test 1: Estados OLED — observa la cara del robot')
    estados = ['ESPERANDO', 'ESCUCHANDO', 'PENSANDO', 'HABLANDO', 'FELIZ', 'CURIOSO']
    for e in estados:
        print(f'  → Enviando ESTADO:{e}')
        mgr.cmd_estado(e)
        time.sleep(2.0)
    mgr.cmd_estado('ESPERANDO')
    print('  ✓ Si viste 6 expresiones distintas, OLED OK')


def prueba_servos(mgr: SerialManager) -> None:
    banner('Test 2: Servos pan/tilt — observa el movimiento')
    posiciones = [
        (90, 90, 'centro'),
        (45, 90, 'izquierda'),
        (135, 90, 'derecha'),
        (90, 90, 'centro'),
        (90, 60, 'arriba'),
        (90, 120, 'abajo'),
        (90, 90, 'centro final'),
    ]
    for pan, tilt, desc in posiciones:
        print(f'  → Pan={pan} Tilt={tilt} ({desc})')
        mgr.cmd_servo(pan, tilt)
        time.sleep(1.5)
    print('  ✓ Si los servos se movieron a las 6 posiciones, servos OK')


def prueba_siguiendo(mgr: SerialManager) -> None:
    banner('Test 3: SIGUIENDO — observa las pupilas del OLED')
    print('  Las pupilas deben moverse en círculo (4 posiciones cardinales)')
    coords = [
        (0.0, 0.0, 'centro'),
        (1.0, 0.0, 'derecha'),
        (0.0, 1.0, 'abajo'),
        (-1.0, 0.0, 'izquierda'),
        (0.0, -1.0, 'arriba'),
        (0.0, 0.0, 'centro'),
    ]
    mgr.cmd_estado('SIGUIENDO')
    time.sleep(0.5)
    for dx, dy, desc in coords:
        print(f'  → dx={dx:+.1f} dy={dy:+.1f} ({desc})')
        mgr.cmd_siguiendo(dx, dy)
        time.sleep(1.5)
    print('  ✓ Si las pupilas siguieron las 4 direcciones, SIGUIENDO OK')


def prueba_throttle(mgr: SerialManager) -> None:
    banner('Test 4: Throttle — spam de comandos sin matar el ESP32')
    print('  Enviando 50 comandos servo en 1 segundo...')
    print('  (el throttle interno debe filtrar a ~20 Hz max)')
    t0 = time.monotonic()
    for i in range(50):
        pan = 90 + (i % 10) * 3
        mgr.cmd_servo(pan, 90)
        time.sleep(0.02)
    dt = time.monotonic() - t0
    print(f'  ✓ 50 comandos encolados en {dt:.2f}s sin bloqueo')
    print('  El servo debe haberse movido sutilmente, sin saturarse')
    time.sleep(2)
    mgr.cmd_servo(90, 90)


def main() -> None:
    print('═' * 60)
    print('  TEST FASE 1 — SerialManager')
    print('═' * 60)
    print(f'\nAbriendo {PUERTO} @ {BAUD} baud...')

    mgr = SerialManager(PUERTO, BAUD)
    time.sleep(2.5)  # esperar a que el ESP32 esté listo después del reset USB

    try:
        prueba_estados(mgr)
        prueba_servos(mgr)
        prueba_siguiendo(mgr)
        prueba_throttle(mgr)

        banner('Test final: volver a estado neutro')
        mgr.cmd_servo(90, 90)
        mgr.cmd_estado('ESPERANDO')
        time.sleep(1.0)
        print('  ✓ Robot en posición neutra, OLED ESPERANDO')

    finally:
        print('\nCerrando SerialManager...')
        mgr.cerrar()
        print('Listo.')


if __name__ == '__main__':
    main()
