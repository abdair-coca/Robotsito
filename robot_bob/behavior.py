"""
behavior.py — Motor de comportamiento humano del robot Bob.

Corre en hilo daemon a 100 ms de tick. Según el estado del robot:

  IDLE / PRESENCE:
    Genera waypoints aleatorios dentro del rango servo con pausas variables.
    Movimiento suave (lerp exponencial). Ocasionales inclinaciones y desvíos.

  PRESENCE (con cara detectada):
    Mezcla tracking real con desvíos de mirada ocasionales (cada 3-8 s).
    Cuando no hay desvío activo, delega la posición objetivo al FacialTracker.

  THINKING:
    Pausa + leve inclinación de tilt (como si pensara).

  SPEAKING:
    Tracking lento con desvíos más frecuentes (no fija mirada rígidamente).

El FacialTracker expone set_objetivo(pan, tilt) y actualizar_servo(det, ...).
BehaviorEngine decide cuándo delegar al tracker y cuándo imponer su propio objetivo.
"""

import math
import random
import threading
import time
from state_machine import RobotState
from facial_tracker import (
    PAN_MIN as _PAN_MIN, PAN_MAX as _PAN_MAX,
    TILT_MIN as _TILT_MIN, TILT_MAX as _TILT_MAX,
    GANANCIA_PAN, GANANCIA_TILT, SIGNO_PAN, SIGNO_TILT,
    ZONA_MUERTA_X, ZONA_MUERTA_Y, ADELANTO_MAX,
)

# Límites servo (deben coincidir con facial_tracker.py)
PAN_MIN,  PAN_MAX  = 20,  160
TILT_MIN, TILT_MAX = 50,  130
PAN_HOME  = 90
TILT_HOME = 90

TICK_S = 0.10  # 10 Hz


class BehaviorEngine:
    def __init__(self, state_machine, facial_tracker):
        self._sm      = state_machine
        self._tracker = facial_tracker
        self._detener = threading.Event()

        # Parámetros de suavizado por estado (alpha del lerp exponencial)
        self.alpha = {
            RobotState.IDLE:              0.04,
            RobotState.PRESENCE:          0.10,
            RobotState.LISTENING:         0.15,
            RobotState.THINKING:          0.06,
            RobotState.SPEAKING:          0.07,
            RobotState.CONVERSATION_IDLE: 0.10,
        }
        # Max paso por tick según estado
        self.max_paso = {
            RobotState.IDLE:              1.2,
            RobotState.PRESENCE:          1.5,
            RobotState.LISTENING:         1.5,
            RobotState.THINKING:          0.5,
            RobotState.SPEAKING:          0.8,
            RobotState.CONVERSATION_IDLE: 1.0,
        }

        # Estado interno del motor
        self._pan_obj   = float(PAN_HOME)
        self._tilt_obj  = float(TILT_HOME)

        # Waypoint idle
        self._wp_pan    = float(PAN_HOME)
        self._wp_tilt   = float(TILT_HOME)
        self._wp_hasta  = 0.0   # tiempo hasta el cual permanecer en waypoint
        self._en_pausa  = False

        # Desvío de mirada
        self._desvio_activo  = False
        self._desvio_pan_off = 0.0
        self._desvio_tilt_off = 0.0
        self._desvio_hasta   = 0.0
        self._prox_desvio    = time.monotonic() + random.uniform(4.0, 8.0)

        self._hilo = threading.Thread(target=self._loop, daemon=True, name='behavior-engine')
        self._hilo.start()

    # ── API pública ────────────────────────────────────────────────────────────

    def cerrar(self) -> None:
        self._detener.set()
        self._hilo.join(timeout=1.0)

    # ── Loop principal ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._detener.is_set():
            t0    = time.monotonic()
            estado = self._sm.estado
            det    = self._tracker.ultimo_det

            self._tick(estado, det, t0)

            elapsed = time.monotonic() - t0
            if elapsed < TICK_S:
                self._detener.wait(TICK_S - elapsed)

    def _tick(self, estado: RobotState, det, ahora: float) -> None:
        alpha    = self.alpha.get(estado, 0.08)
        max_paso = self.max_paso.get(estado, 1.0)

        if estado in (RobotState.IDLE, RobotState.PRESENCE) and det is None:
            self._tick_idle(ahora)

        elif estado == RobotState.PRESENCE and det is not None:
            self._tick_presence(det, ahora)

        elif estado == RobotState.THINKING:
            self._tick_thinking()

        elif estado in (RobotState.LISTENING, RobotState.CONVERSATION_IDLE):
            # Tracking normal si hay cara, sino mantener posición
            if det is not None:
                self._tracker.actualizar_servo(det, suavizado=0.85, max_paso_pan=max_paso, max_paso_tilt=max_paso)
            return  # tracker ya envió el comando

        elif estado == RobotState.SPEAKING:
            self._tick_speaking(det, ahora)

        else:
            # IDLE sin cara → usar waypoints
            self._tick_idle(ahora)
            return

        # Aplicar objetivo al tracker
        self._tracker.set_objetivo(self._pan_obj, self._tilt_obj)
        self._tracker.actualizar_servo(None, suavizado=1.0 - alpha, max_paso_pan=max_paso, max_paso_tilt=max_paso)

    # ── Sub-comportamientos ────────────────────────────────────────────────────

    def _tick_idle(self, ahora: float) -> None:
        """Movimiento 'aburrido': waypoints aleatorios con pausas."""
        if self._en_pausa:
            if ahora < self._wp_hasta:
                # Permanecer en waypoint actual (objetivo ya seteado)
                self._pan_obj  = self._wp_pan
                self._tilt_obj = self._wp_tilt
                return
            self._en_pausa = False

        # Llegamos al waypoint o es el inicio — elegir uno nuevo
        dist = math.hypot(self._tracker.pan_actual - self._wp_pan,
                          self._tracker.tilt_actual - self._wp_tilt)
        if dist < 2.0:
            # Cerca del waypoint: pausar
            pausa = random.uniform(0.5, 3.0)
            self._wp_hasta = ahora + pausa
            self._en_pausa = True
            # Pequeña variación casual de posición (microsacada de 1-3°)
            self._wp_pan  = _clamp(self._wp_pan  + random.gauss(0, 1.5), PAN_MIN,  PAN_MAX)
            self._wp_tilt = _clamp(self._wp_tilt + random.gauss(0, 1.0), TILT_MIN, TILT_MAX)

        # Calcular waypoint nuevo aleatorio (zona central con preferencia)
        if not self._en_pausa:
            # Distribución normal centrada en PAN_HOME/TILT_HOME con ±35/±25 de sigma
            self._wp_pan  = _clamp(random.gauss(PAN_HOME,  35), PAN_MIN  + 10, PAN_MAX  - 10)
            self._wp_tilt = _clamp(random.gauss(TILT_HOME, 25), TILT_MIN + 5,  TILT_MAX - 5)

        self._pan_obj  = self._wp_pan
        self._tilt_obj = self._wp_tilt

    def _tick_presence(self, det, ahora: float) -> None:
        """Tracking con desvíos ocasionales de mirada."""
        cx, cy, *_ = det
        # Calcular posición "real" que seguiría al tracking
        err_x = cx - self._tracker.cx_centro
        err_y = cy - self._tracker.cy_centro

        d_pan  = SIGNO_PAN  * GANANCIA_PAN  * err_x if abs(err_x) > ZONA_MUERTA_X else 0
        d_tilt = SIGNO_TILT * GANANCIA_TILT * err_y if abs(err_y) > ZONA_MUERTA_Y else 0

        pan_real  = _clamp(self._tracker.pan_obj  + d_pan,  _PAN_MIN, _PAN_MAX)
        tilt_real = _clamp(self._tracker.tilt_obj + d_tilt, _TILT_MIN, _TILT_MAX)

        # Desvío de mirada
        if self._desvio_activo:
            if ahora > self._desvio_hasta:
                self._desvio_activo   = False
                self._desvio_pan_off  = 0.0
                self._desvio_tilt_off = 0.0
                self._prox_desvio = ahora + random.uniform(3.0, 8.0)
        else:
            if ahora >= self._prox_desvio:
                angulo_max = 15.0
                self._desvio_pan_off  = random.uniform(-angulo_max, angulo_max)
                self._desvio_tilt_off = random.uniform(-angulo_max * 0.5, angulo_max * 0.5)
                self._desvio_hasta    = ahora + random.uniform(0.8, 2.0)
                self._desvio_activo   = True

        self._pan_obj  = _clamp(pan_real  + self._desvio_pan_off,  _PAN_MIN, _PAN_MAX)
        self._tilt_obj = _clamp(tilt_real + self._desvio_tilt_off, _TILT_MIN, _TILT_MAX)

        # Actualizar los objetivos internos del tracker (necesario para el cálculo de siguiente tick)
        self._tracker.pan_obj  = pan_real
        self._tracker.tilt_obj = tilt_real

    def _tick_thinking(self) -> None:
        """Leve inclinación del tilt hacia arriba-derecha (como pensando)."""
        self._pan_obj  = _clamp(self._tracker.pan_actual  + 5.0, PAN_MIN,  PAN_MAX)
        self._tilt_obj = _clamp(self._tracker.tilt_actual - 5.0, TILT_MIN, TILT_MAX)

    def _tick_speaking(self, det, ahora: float) -> None:
        """Tracking lento + desvíos más frecuentes."""
        if det is not None:
            self._tick_presence(det, ahora)
        else:
            self._pan_obj  = self._tracker.pan_actual
            self._tilt_obj = self._tracker.tilt_actual


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v
