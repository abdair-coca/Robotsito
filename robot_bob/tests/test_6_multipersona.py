"""
test_6_multipersona.py — Simulación de flujo de feria con varias personas pasando.

Sin cámara, sin voz, sin red. Solo SerialManager + StateMachine + BehaviorEngine
+ MockTracker que reproduce un GUION SCRIPTED de aparición de caras.

Qué valida:
  - Transiciones IDLE↔PRESENCE con varias personas en sucesión
  - Cooldown de conversación NO se viola (este test fuerza P_CONV=0)
  - "Doble toma" se gatilla SOLO cuando hay >30 s sin presencia entre personas
  - Stream rápido de caras (3 caras en 4 s) no rompe la máquina
  - Cierre limpio sin estados huérfanos

Necesita: COM3 conectado (ESP32 con OLED y servos). NO necesita ESP32-CAM.

Ejecutar:
  cd robot_bob
  python tests/test_6_multipersona.py

Duración total: ~80 s. No interactivo (mira el OLED + servos para validar visualmente).
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

# Tiempo entre updates del loop principal (simula los 25 FPS del main)
LOOP_DT = 0.05  # 20 Hz


class MockTracker:
    """API mínima del FacialTracker para BehaviorEngine, sin cámara."""
    def __init__(self, serial_mgr):
        self._serial = serial_mgr
        self.w, self.h = 320, 240
        self.cx_centro, self.cy_centro = 160, 120
        self.pan_actual  = float(PAN_HOME)
        self.tilt_actual = float(TILT_HOME)
        self.pan_obj     = float(PAN_HOME)
        self.tilt_obj    = float(TILT_HOME)
        self._lock = threading.Lock()
        self._det = None

    @property
    def ultimo_det(self):
        with self._lock:
            return self._det

    def set_cara(self, det):
        with self._lock:
            self._det = det

    def set_objetivo(self, pan, tilt):
        self.pan_obj  = max(PAN_MIN, min(PAN_MAX, pan))
        self.tilt_obj = max(TILT_MIN, min(TILT_MAX, tilt))

    def actualizar_servo(self, det, suavizado=0.85, max_paso_pan=1.5, max_paso_tilt=1.5):
        if det is not None:
            cx, cy, *_ = det
            err_x = cx - self.cx_centro
            err_y = cy - self.cy_centro
            d_pan  = SIGNO_PAN  * GANANCIA_PAN  * err_x if abs(err_x) > ZONA_MUERTA_X else 0
            d_tilt = SIGNO_TILT * GANANCIA_TILT * err_y if abs(err_y) > ZONA_MUERTA_Y else 0
            self.pan_obj  = max(PAN_MIN, min(PAN_MAX, self.pan_obj  + d_pan))
            self.tilt_obj = max(TILT_MIN, min(TILT_MAX, self.tilt_obj + d_tilt))
        # Linear interp suficiente para el test (el easing del real está aparte)
        pan_s  = suavizado * self.pan_actual  + (1 - suavizado) * self.pan_obj
        tilt_s = suavizado * self.tilt_actual + (1 - suavizado) * self.tilt_obj
        dpan  = max(-max_paso_pan,  min(max_paso_pan,  pan_s  - self.pan_actual))
        dtilt = max(-max_paso_tilt, min(max_paso_tilt, tilt_s - self.tilt_actual))
        self.pan_actual  += dpan
        self.tilt_actual += dtilt
        self._serial.cmd_servo(self.pan_actual, self.tilt_actual)


# ── Guión: lista de ventanas (t_inicio, t_fin, descripción, generador_pos) ────
# generador_pos(t_local) → (cx, cy) en frame 320x240. None significa "nadie".

def _quieto(cx, cy):
    return lambda t: (cx, cy)

def _movimiento_lateral(t_dur):
    return lambda t: (160 + int(math.sin(t / t_dur * math.pi) * 80), 120)

GUION = [
    # (t_ini, t_fin,  etiqueta,                      generador)
    ( 0.0,   5.0,  'IDLE warmup (nadie)',           None),
    ( 5.0,  10.0,  'Persona A — quieta centro',     _quieto(160, 110)),
    (10.0,  15.0,  'Nadie — vuelve a IDLE',         None),
    (15.0,  20.0,  'Persona B — moviéndose',        _movimiento_lateral(5.0)),
    (20.0,  50.0,  'Nadie — largo gap >30s',        None),
    (50.0,  56.0,  'Persona C — DEBE doble toma',   _quieto(170, 105)),
    (56.0,  60.0,  'Nadie (gap corto)',             None),
    (60.0,  64.0,  'Persona D — sin doble toma',    _quieto(150, 115)),
    (64.0,  66.0,  'Nadie',                         None),
    (66.0,  67.0,  'Persona E (1s)',                _quieto(170, 115)),
    (67.0,  68.0,  'Nadie',                         None),
    (68.0,  69.0,  'Persona F (1s)',                _quieto(150, 115)),
    (69.0,  70.0,  'Nadie',                         None),
    (70.0,  71.0,  'Persona G (1s)',                _quieto(160, 110)),
    (71.0,  76.0,  'Nadie — cierre',                None),
]


class Resultado:
    def __init__(self):
        self.transiciones    = []  # (t, estado_str)
        self.startles        = []  # timestamps en que startle_active fue True
        self.frames_total    = 0
        self.frames_con_cara = 0
        self.estado_actual   = None
        self.startle_prev    = False

    def registrar(self, t: float, estado_str: str, startle: bool, con_cara: bool):
        if self.estado_actual != estado_str:
            self.transiciones.append((t, estado_str))
            self.estado_actual = estado_str
        if startle and not self.startle_prev:
            self.startles.append(t)
        self.startle_prev = startle
        self.frames_total += 1
        if con_cara:
            self.frames_con_cara += 1


def main() -> None:
    print('═' * 60)
    print('  TEST 6 — Multi-persona (flujo feria simulado)')
    print('═' * 60)

    mgr = SerialManager(PUERTO, BAUD)
    time.sleep(2.5)
    mgr.cmd_estado('ESPERANDO')
    mgr.cmd_servo(PAN_HOME, TILT_HOME)
    time.sleep(0.5)

    # P_CONV=0 → nunca arranca conversación automática. Así NO interfiere con el test.
    sm       = StateMachine(mgr, p_conversacion=0.0,
                            t_permanencia_min=2.0,  # más rápido para el test
                            cooldown_conv=30.0,
                            conversation_timeout=5.0,
                            timeout_rostro=1.5)
    tracker  = MockTracker(mgr)
    behavior = BehaviorEngine(sm, tracker)
    resultado = Resultado()

    duracion_total = GUION[-1][1]
    t0 = time.monotonic()
    idx_actual = -1

    print(f'\nGuión: {len(GUION)} ventanas, duración {duracion_total:.0f}s')
    print('(observa el OLED + servos. El estado se imprime cuando cambia)\n')

    try:
        while True:
            ahora = time.monotonic() - t0
            if ahora >= duracion_total:
                break

            # Encontrar ventana activa
            for i, (ini, fin, etiq, gen) in enumerate(GUION):
                if ini <= ahora < fin:
                    if i != idx_actual:
                        idx_actual = i
                        print(f'  [t={ahora:5.1f}s] {etiq}')
                    if gen is None:
                        tracker.set_cara(None)
                        sm.notificar_cara(False)
                    else:
                        cx, cy = gen(ahora - ini)
                        # Bounding box ficticio alrededor del centro
                        det = (cx, cy, cx - 30, cy - 40, 60, 80)
                        tracker.set_cara(det)
                        sm.notificar_cara(True)
                    break

            resultado.registrar(
                ahora,
                sm.estado.value,
                sm.startle_active(),
                tracker.ultimo_det is not None,
            )

            time.sleep(LOOP_DT)

    except KeyboardInterrupt:
        print('\n(interrumpido)')

    finally:
        print('\nCerrando...')
        behavior.cerrar()
        mgr.cmd_servo(PAN_HOME, TILT_HOME)
        time.sleep(0.3)
        mgr.cmd_estado('ESPERANDO')
        time.sleep(0.1)
        mgr.cerrar()

        # ── Reporte ───────────────────────────────────────────────────────
        print('\n' + '═' * 60)
        print('  REPORTE TEST 6 — Multi-persona')
        print('═' * 60)
        print(f'Frames procesados:  {resultado.frames_total}')
        print(f'Frames con cara:    {resultado.frames_con_cara} '
              f'({100*resultado.frames_con_cara/max(1,resultado.frames_total):.0f}%)')
        print(f'Transiciones:       {len(resultado.transiciones)}')
        for t, est in resultado.transiciones:
            print(f'  [{t:5.1f}s] → {est}')
        print(f'\nStartles (doble toma): {len(resultado.startles)}')
        for t in resultado.startles:
            print(f'  [{t:5.1f}s] startle activado')

        # ── Validaciones automáticas ──────────────────────────────────────
        print('\nCriterios de aceptación:')

        # 1: debe haber alternado IDLE↔PRESENCE varias veces (al menos 5)
        n_presence = sum(1 for _, e in resultado.transiciones if e == 'PRESENCE')
        ok_alt = n_presence >= 5
        print(f'  {"✓" if ok_alt else "✗"} Alternancia IDLE↔PRESENCE  '
              f'({n_presence} entradas a PRESENCE, esperaba ≥5)')

        # 2: startle debe disparar EXACTAMENTE 1 vez, alrededor del segundo 50
        ok_startle_n = len(resultado.startles) == 1
        ok_startle_t = ok_startle_n and 49.5 < resultado.startles[0] < 52.0
        if ok_startle_n and ok_startle_t:
            print('  ✓ Doble toma  (1 startle alrededor de t=50s)')
        elif len(resultado.startles) == 0:
            print('  ✗ Doble toma  (NUNCA se gatilló — bug en startle_active)')
        elif len(resultado.startles) > 1:
            print(f'  ✗ Doble toma  ({len(resultado.startles)} startles — falsos positivos)')
        else:
            print(f'  ✗ Doble toma  (startle en t={resultado.startles[0]:.1f}s, '
                  f'esperaba ~50s)')

        # 3: ninguna transición a LISTENING (p_conv=0 y nadie dijo "Bob")
        n_listen = sum(1 for _, e in resultado.transiciones if e == 'LISTENING')
        ok_no_conv = n_listen == 0
        print(f'  {"✓" if ok_no_conv else "✗"} Sin conversaciones espurias  '
              f'({n_listen} LISTENING, esperaba 0)')

        # 4: estado final debe ser IDLE
        ok_final = sm.estado == RobotState.IDLE
        print(f'  {"✓" if ok_final else "✗"} Estado final IDLE  '
              f'(actual: {sm.estado.value})')

        todo_ok = ok_alt and ok_startle_n and ok_startle_t and ok_no_conv and ok_final
        print('\n' + ('✅ TEST 6 PASADO' if todo_ok else '❌ TEST 6 FALLÓ'))
        print('═' * 60)


if __name__ == '__main__':
    main()
