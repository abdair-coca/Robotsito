"""
test_4_behavior.py — Prueba aislada del BehaviorEngine (sin cámara, sin voz).

Qué prueba:
  1. IDLE: waypoints amplios + pausas, NO oscila en home (regresión del bug fixeado)
  2. PRESENCE simulada: tracking de una cara virtual con desvíos
  3. THINKING: leve inclinación hacia arriba
  4. SPEAKING: tracking lento + desvíos más frecuentes
  5. Transiciones entre estados sin errores

NO arranca: stream, cámara, MediaPipe, voz. Solo SerialManager + StateMachine + Behavior.

Usa un MockTracker que simula la API del FacialTracker (pan/tilt actuales/objetivos,
ultimo_det, set_objetivo, actualizar_servo). Esto permite ver el comportamiento en el
OLED y servos físicos sin depender del ESP32-CAM.

Ejecutar:
  cd robot_bob
  python tests/test_4_behavior.py

Salir: Ctrl+C en cualquier momento.
"""

import os
import sys
import time
import math
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from serial_manager import SerialManager
from state_machine  import StateMachine, RobotState
from behavior       import BehaviorEngine
from facial_tracker import (
    PAN_MIN, PAN_MAX, TILT_MIN, TILT_MAX, PAN_HOME, TILT_HOME,
    SIGNO_PAN, SIGNO_TILT, GANANCIA_PAN, GANANCIA_TILT,
    ZONA_MUERTA_X, ZONA_MUERTA_Y,
)

PUERTO = 'COM3'
BAUD   = 115200


def banner(msg: str) -> None:
    print('\n' + '=' * 60)
    print(f'  {msg}')
    print('=' * 60)


class MockTracker:
    """
    Simula la API mínima de FacialTracker que el BehaviorEngine necesita.
    No abre cámara, no detecta caras. La "cara virtual" se mueve programáticamente.
    """

    def __init__(self, serial_mgr):
        self._serial = serial_mgr
        # Resolución simulada (igual a la real del ESP32-CAM)
        self.w = 320
        self.h = 240
        self.cx_centro = self.w // 2
        self.cy_centro = self.h // 2
        # Estado servo (lo que BehaviorEngine va a leer/escribir)
        self.pan_actual  = float(PAN_HOME)
        self.tilt_actual = float(TILT_HOME)
        self.pan_obj     = float(PAN_HOME)
        self.tilt_obj    = float(TILT_HOME)
        # Cara virtual (cx, cy, x, y, w, h) | None
        self._det_lock = threading.Lock()
        self._det = None
        # Métricas
        self.muestras_pan  = []
        self.muestras_tilt = []

    @property
    def ultimo_det(self):
        with self._det_lock:
            return self._det

    def set_cara_virtual(self, det):
        with self._det_lock:
            self._det = det

    def set_objetivo(self, pan: float, tilt: float) -> None:
        self.pan_obj  = max(PAN_MIN, min(PAN_MAX, pan))
        self.tilt_obj = max(TILT_MIN, min(TILT_MAX, tilt))

    def actualizar_servo(self, det, suavizado: float = 0.85,
                         max_paso_pan: float = 1.5, max_paso_tilt: float = 1.5) -> None:
        if det is not None:
            cx, cy, *_ = det
            err_x = cx - self.cx_centro
            err_y = cy - self.cy_centro
            d_pan  = SIGNO_PAN  * GANANCIA_PAN  * err_x if abs(err_x) > ZONA_MUERTA_X else 0
            d_tilt = SIGNO_TILT * GANANCIA_TILT * err_y if abs(err_y) > ZONA_MUERTA_Y else 0
            self.pan_obj  = max(PAN_MIN, min(PAN_MAX, self.pan_obj  + d_pan))
            self.tilt_obj = max(TILT_MIN, min(TILT_MAX, self.tilt_obj + d_tilt))

        pan_s  = suavizado * self.pan_actual  + (1 - suavizado) * self.pan_obj
        tilt_s = suavizado * self.tilt_actual + (1 - suavizado) * self.tilt_obj
        dpan   = max(-max_paso_pan,  min(max_paso_pan,  pan_s  - self.pan_actual))
        dtilt  = max(-max_paso_tilt, min(max_paso_tilt, tilt_s - self.tilt_actual))
        self.pan_actual  = max(PAN_MIN, min(PAN_MAX, self.pan_actual  + dpan))
        self.tilt_actual = max(TILT_MIN, min(TILT_MAX, self.tilt_actual + dtilt))

        self.muestras_pan.append(self.pan_actual)
        self.muestras_tilt.append(self.tilt_actual)
        self._serial.cmd_servo(self.pan_actual, self.tilt_actual)


def prueba_idle(mgr: SerialManager, sm: StateMachine, tracker: MockTracker,
                behavior: BehaviorEngine) -> None:
    banner('Test IDLE: waypoints amplios + pausas largas (regresión bug)')
    print('  Observa el servo: debe hacer 2-3 giros AMPLIOS (>15°) en 20 s,')
    print('  con pausas claras entre uno y otro. NO debe vibrar en (90,90).')
    print()

    # Forzar IDLE y limpiar métricas
    sm._transicionar(RobotState.IDLE)
    tracker.set_cara_virtual(None)
    tracker.muestras_pan.clear()
    tracker.muestras_tilt.clear()

    t0 = time.monotonic()
    dur = 20.0
    while time.monotonic() - t0 < dur:
        rest = dur - (time.monotonic() - t0)
        print(f'\r  t={time.monotonic()-t0:5.1f}s  '
              f'Pan={tracker.pan_actual:5.1f}  Tilt={tracker.tilt_actual:5.1f}  '
              f'restan {rest:4.1f}s ', end='', flush=True)
        time.sleep(0.2)
    print()

    # Validación automática: rango de pan visitado debe ser > 20° (no quedó pegado)
    if not tracker.muestras_pan:
        print('  ✗ No se registraron muestras de pan — BehaviorEngine no actuó')
        return
    pan_min, pan_max = min(tracker.muestras_pan), max(tracker.muestras_pan)
    tilt_min, tilt_max = min(tracker.muestras_tilt), max(tracker.muestras_tilt)
    rango_pan  = pan_max  - pan_min
    rango_tilt = tilt_max - tilt_min
    print(f'  Rango pan visitado:  {pan_min:5.1f} → {pan_max:5.1f}  (Δ={rango_pan:5.1f}°)')
    print(f'  Rango tilt visitado: {tilt_min:5.1f} → {tilt_max:5.1f}  (Δ={rango_tilt:5.1f}°)')
    if rango_pan >= 20.0:
        print('  ✓ IDLE OK — el robot exploró un rango amplio')
    else:
        print('  ✗ FALLA: rango pan < 20° — el robot quedó pegado cerca de home')


def prueba_presence(mgr: SerialManager, sm: StateMachine, tracker: MockTracker,
                    behavior: BehaviorEngine) -> None:
    banner('Test PRESENCE simulada: cara virtual que se mueve')
    print('  Una "cara" virtual se moverá de izquierda → centro → derecha en 12 s.')
    print('  El servo debería seguirla (con suavizado y posibles desvíos).')
    print()

    sm._transicionar(RobotState.PRESENCE)
    tracker.muestras_pan.clear()

    t0 = time.monotonic()
    dur = 12.0
    while time.monotonic() - t0 < dur:
        t = time.monotonic() - t0
        # Cara que oscila horizontalmente con un seno + posición tilt fija
        cx = int(tracker.cx_centro + math.sin(t * 0.6) * 80)
        cy = int(tracker.cy_centro - 10)
        det = (cx, cy, cx - 30, cy - 40, 60, 80)
        tracker.set_cara_virtual(det)
        print(f'\r  t={t:5.1f}s  cara cx={cx:3d}  '
              f'Pan={tracker.pan_actual:5.1f}  Tilt={tracker.tilt_actual:5.1f}  ',
              end='', flush=True)
        time.sleep(0.1)
    print()

    tracker.set_cara_virtual(None)
    rango_pan = max(tracker.muestras_pan) - min(tracker.muestras_pan)
    print(f'  Rango pan durante tracking: {rango_pan:5.1f}°')
    if rango_pan >= 10.0:
        print('  ✓ PRESENCE OK — el robot siguió la cara virtual')
    else:
        print('  ✗ FALLA: pan no se movió lo suficiente para seguir la cara')


def prueba_thinking(mgr: SerialManager, sm: StateMachine, tracker: MockTracker,
                    behavior: BehaviorEngine) -> None:
    banner('Test THINKING: leve inclinación hacia arriba')
    print('  Servo debería inclinarse ligeramente hacia arriba-derecha (5°).')
    print()

    pan_inicio  = tracker.pan_actual
    tilt_inicio = tracker.tilt_actual
    sm._transicionar(RobotState.THINKING)
    tracker.set_cara_virtual(None)

    t0 = time.monotonic()
    while time.monotonic() - t0 < 5.0:
        print(f'\r  t={time.monotonic()-t0:4.1f}s  '
              f'Pan={tracker.pan_actual:5.1f} (Δ{tracker.pan_actual-pan_inicio:+5.1f})  '
              f'Tilt={tracker.tilt_actual:5.1f} (Δ{tracker.tilt_actual-tilt_inicio:+5.1f})  ',
              end='', flush=True)
        time.sleep(0.2)
    print()

    d_tilt = tracker.tilt_actual - tilt_inicio
    if d_tilt < -1.0:  # tilt negativo = mira más arriba (según convención en behavior.py)
        print(f'  ✓ THINKING OK — se inclinó {d_tilt:.1f}° hacia arriba')
    else:
        print(f'  ⚠ THINKING: Δtilt = {d_tilt:+.1f}° (esperaba <-1°)')


def prueba_speaking(mgr: SerialManager, sm: StateMachine, tracker: MockTracker,
                    behavior: BehaviorEngine) -> None:
    banner('Test SPEAKING: tracking lento con cara virtual')
    print('  La cara queda fija pero el servo debería seguirla lentamente.')
    print()

    sm._transicionar(RobotState.SPEAKING)
    # Cara fija en un costado
    cx = tracker.cx_centro + 50
    cy = tracker.cy_centro
    tracker.set_cara_virtual((cx, cy, cx - 30, cy - 40, 60, 80))

    t0 = time.monotonic()
    while time.monotonic() - t0 < 6.0:
        print(f'\r  t={time.monotonic()-t0:4.1f}s  '
              f'Pan={tracker.pan_actual:5.1f}  Tilt={tracker.tilt_actual:5.1f}  ',
              end='', flush=True)
        time.sleep(0.2)
    print()

    tracker.set_cara_virtual(None)
    print('  ✓ SPEAKING OK si viste el servo deslizarse hacia el costado')


def main() -> None:
    print('═' * 60)
    print('  TEST FASE 5 — BehaviorEngine (sin cámara, sin voz)')
    print('═' * 60)
    print(f'\nAbriendo {PUERTO}...')

    mgr = SerialManager(PUERTO, BAUD)
    time.sleep(2.5)  # esperar reset del ESP32
    mgr.cmd_estado('ESPERANDO')
    mgr.cmd_servo(PAN_HOME, TILT_HOME)
    time.sleep(0.5)

    sm      = StateMachine(mgr, p_conversacion=0.0)  # nunca arranca conversación
    tracker = MockTracker(mgr)
    behavior = BehaviorEngine(mgr, sm, tracker)

    try:
        prueba_idle(mgr, sm, tracker, behavior)
        prueba_presence(mgr, sm, tracker, behavior)
        prueba_thinking(mgr, sm, tracker, behavior)
        prueba_speaking(mgr, sm, tracker, behavior)

        banner('Cierre limpio')
    except KeyboardInterrupt:
        print('\n(interrumpido por usuario)')

    finally:
        print('\nCerrando BehaviorEngine...')
        behavior.cerrar()
        mgr.cmd_servo(PAN_HOME, TILT_HOME)
        time.sleep(0.3)
        mgr.cmd_estado('ESPERANDO')
        time.sleep(0.1)
        mgr.cerrar()
        print('Listo.')


if __name__ == '__main__':
    main()
