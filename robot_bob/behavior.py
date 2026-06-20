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
from expression_engine import random_mueca
from facial_tracker import (
    PAN_MIN as _PAN_MIN, PAN_MAX as _PAN_MAX,
    TILT_MIN as _TILT_MIN, TILT_MAX as _TILT_MAX,
    GANANCIA_PAN, GANANCIA_TILT, SIGNO_PAN, SIGNO_TILT,
    ZONA_MUERTA_X, ZONA_MUERTA_Y, ADELANTO_MAX,
)

# Config de muecas (en config.py, gitignoreado). Fallback por si falta.
try:
    from config import (MUECA_ENABLED, MUECA_HEAD_ENABLED, MUECA_HEAD_AMPL,
                        MUECA_COOLDOWN_S, P_MUECA)
except Exception:
    MUECA_ENABLED, MUECA_HEAD_ENABLED = False, False
    MUECA_HEAD_AMPL, MUECA_COOLDOWN_S, P_MUECA = 6.0, 8.0, 0.30

# Config de locomoción (giro del cuerpo hacia la cara, Fase 3). Fallback si falta.
try:
    from config import (MOTORES_ENABLED, GIRO_VELOCIDAD, GIRO_PAN_THRESHOLD,
                        GIRO_BURST_S, GIRO_COOLDOWN_S, GIRO_INVERTIR, MOTOR_DEBUG,
                        GIRO_IDLE_ENABLED, GIRO_IDLE_COOLDOWN_S, GIRO_IDLE_PROB)
except Exception:
    MOTORES_ENABLED = False
    GIRO_VELOCIDAD = 70
    GIRO_PAN_THRESHOLD, GIRO_BURST_S, GIRO_COOLDOWN_S, GIRO_INVERTIR = 30, 0.40, 0.4, False
    MOTOR_DEBUG = False
    GIRO_IDLE_ENABLED, GIRO_IDLE_COOLDOWN_S, GIRO_IDLE_PROB = True, 6.0, 0.4

# Comandos de giro sobre el eje (izq, der) para mover_motores del firmware.
_GIRO_IZQ = (-1, 1)
_GIRO_DER = (1, -1)

# Límites servo (deben coincidir con facial_tracker.py)
PAN_MIN,  PAN_MAX  = 20,  160
TILT_MIN, TILT_MAX = 40,  130   # TILT_MIN bajo = mira más arriba (límite firmware = 40)
PAN_HOME  = 90
TILT_HOME = 90

TICK_S = 0.05  # 20 Hz — más suave para los servos sin saturar CPU

# Offset vertical: la cámara apunta al centro del bbox (cuello/boca).
# La gente espera contacto visual a los ojos → subimos la mirada N grados.
# (tilt negativo = mirar más arriba). Tuneable en config.py.
try:
    from config import EYE_CONTACT_OFFSET_TILT, IDLE_TILT_CENTER, IDLE_TILT_SPREAD
except Exception:
    EYE_CONTACT_OFFSET_TILT = -22.0
    IDLE_TILT_CENTER, IDLE_TILT_SPREAD = 62.0, 16.0

# Postura de sueño: cabeza baja (mentón al pecho)
SLEEP_TILT  = TILT_HOME + 15.0   # 105° — dentro del rango TILT_MAX=130
SLEEP_PAN   = float(PAN_HOME)    # alineada al centro

# Despertar: el tilt sube desde SLEEP_TILT hasta este offset sobre HOME (mirar arriba con CURIOSO)
WAKE_TILT_PEAK = TILT_HOME - 18.0  # 72° — mira hacia arriba mientras se despierta


class BehaviorEngine:
    def __init__(self, state_machine, facial_tracker):
        self._sm      = state_machine
        self._tracker = facial_tracker
        self._detener = threading.Event()
        # Tracking interno para emitir OLED CURIOSO una sola vez al empezar el despertar
        self._startle_prev  = False
        self._asleep_prev   = False
        self._thinking_prev = False

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

        # Muecas (Fase B): scheduler + micro-gesto de cabeza
        self._mueca_next_eval  = time.monotonic() + 1.0
        self._mueca_last       = 0.0
        self._mueca_head_until = 0.0
        self._mueca_pan_off    = 0.0
        self._mueca_tilt_off   = 0.0

        # Giro de cuerpo (Fase 3): máquina de ráfaga corta + cooldown
        self._giro_dir            = None   # (izq,der) en ráfaga, o None
        self._giro_hasta          = 0.0    # fin de la ráfaga actual
        self._giro_cooldown_hasta = 0.0    # hasta cuándo no re-evaluar
        # Pivots de búsqueda en IDLE
        self._giro_idle_last      = 0.0
        self._giro_idle_next_eval = 0.0

        self._hilo = threading.Thread(target=self._loop, daemon=True, name='behavior-engine')
        self._hilo.start()

    # ── API pública ────────────────────────────────────────────────────────────

    def cerrar(self) -> None:
        self._detener.set()
        self._hilo.join(timeout=1.0)
        # Parar motores al cerrar (el watchdog igual los pararía, pero explícito mejor).
        try:
            self._sm._serial.cmd_motor(0, 0)
        except Exception:
            pass

    # ── Loop principal ─────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._detener.is_set():
            t0    = time.monotonic()
            estado = self._sm.estado
            det    = self._tracker.ultimo_det

            self._tick(estado, det, t0)
            self._maybe_mueca(estado, t0)

            elapsed = time.monotonic() - t0
            if elapsed < TICK_S:
                self._detener.wait(TICK_S - elapsed)

    def _giro_tick(self, ahora: float) -> bool:
        """Maneja la ráfaga de giro activa (alimenta el watchdog) y su transición a
        cooldown. Devuelve True si hay ráfaga o cooldown en curso (no disparar otra)."""
        if self._giro_dir is not None:
            if ahora < self._giro_hasta:
                try:
                    self._sm._serial.cmd_motor(*self._giro_dir)
                except Exception:
                    pass
                return True
            self._parar_giro()
            self._giro_cooldown_hasta = ahora + GIRO_COOLDOWN_S
            return True
        return ahora < self._giro_cooldown_hasta

    def _arrancar_giro(self, base, ahora: float) -> None:
        """Arranca una ráfaga de giro en el sentido `base` (±1,±1), a GIRO_VELOCIDAD."""
        giro = (base[0] * GIRO_VELOCIDAD, base[1] * GIRO_VELOCIDAD)
        self._giro_dir   = giro
        self._giro_hasta = ahora + GIRO_BURST_S
        try:
            self._sm._serial.cmd_motor(*giro)
        except Exception:
            pass

    def _maybe_girar_cuerpo(self, det, ahora: float) -> None:
        """PRESENCE: si el pan se desvió del centro, gira el cuerpo para reencarar a
        la persona → la cabeza vuelve sola al centro. NO traslada."""
        if not MOTORES_ENABLED or self._giro_tick(ahora):
            return
        desvio = self._tracker.pan_actual - PAN_HOME
        base   = None
        if desvio > GIRO_PAN_THRESHOLD:
            base = _GIRO_DER if GIRO_INVERTIR else _GIRO_IZQ
        elif desvio < -GIRO_PAN_THRESHOLD:
            base = _GIRO_IZQ if GIRO_INVERTIR else _GIRO_DER
        if MOTOR_DEBUG:
            print('[giro] pan=%.0f desvio=%+.0f -> %s' % (
                self._tracker.pan_actual, desvio, base))
        if base is not None:
            self._arrancar_giro(base, ahora)

    def _maybe_girar_idle(self, ahora: float) -> None:
        """IDLE: pivots de 'mirar alrededor' cada tanto, para que Bob use el cuerpo
        (no solo la cabeza) estando solo. En el lugar, en ráfagas cortas."""
        if not (MOTORES_ENABLED and GIRO_IDLE_ENABLED) or self._giro_tick(ahora):
            return
        if self._sm.is_asleep():
            return
        if ahora < self._giro_idle_next_eval:
            return
        self._giro_idle_next_eval = ahora + 1.0
        if ahora - self._giro_idle_last < GIRO_IDLE_COOLDOWN_S:
            return
        if random.random() > GIRO_IDLE_PROB:
            return
        self._arrancar_giro(random.choice((_GIRO_IZQ, _GIRO_DER)), ahora)
        self._giro_idle_last = ahora

    def _parar_giro(self) -> None:
        if self._giro_dir is not None:
            try:
                self._sm._serial.cmd_motor(0, 0)
            except Exception:
                pass
            self._giro_dir = None

    def _maybe_mueca(self, estado: RobotState, ahora: float) -> None:
        """Dispara muecas espontáneas (cara + micro-gesto de cabeza) cuando Bob
        está solo en IDLE. Evalúa ~1 Hz. No bloquea."""
        if not MUECA_ENABLED or ahora < self._mueca_next_eval:
            return
        self._mueca_next_eval = ahora + 1.0
        # Solo solo y libre; no pisar otro OLED expresivo (soliloquio).
        if estado != RobotState.IDLE or self._sm.en_conversacion:
            return
        # Dormido no hace muecas (rompería la pose/ilusión de sueño).
        if self._sm.is_asleep():
            return
        if self._sm.oled_ocupado():
            return
        if ahora - self._mueca_last < MUECA_COOLDOWN_S:
            return
        if random.random() > P_MUECA:
            return

        dur_ms = random_mueca(self._sm._serial, self._sm)
        self._sm.oled_ocupar(dur_ms)
        self._mueca_last = ahora
        print('[mueca]')

        # Micro-gesto de cabeza suave (se salta dormido: brownout + romper pose).
        if MUECA_HEAD_ENABLED and not self._sm.is_asleep():
            self._mueca_pan_off    = random.uniform(-MUECA_HEAD_AMPL, MUECA_HEAD_AMPL)
            self._mueca_tilt_off   = random.uniform(-MUECA_HEAD_AMPL, MUECA_HEAD_AMPL)
            self._mueca_head_until = ahora + dur_ms / 1000.0

    def _tick(self, estado: RobotState, det, ahora: float) -> None:
        alpha    = self.alpha.get(estado, 0.08)
        max_paso = self.max_paso.get(estado, 1.0)

        # Reset flags one-shot al salir de su estado
        if estado != RobotState.THINKING:
            self._thinking_prev = False

        # Seguridad: solo se permite girar en PRESENCE o IDLE; en charla/pensar, parar.
        if self._giro_dir is not None and estado not in (
                RobotState.PRESENCE, RobotState.IDLE):
            self._parar_giro()

        # ── Despertar: cara nueva tras >SLEEP_THR_S sin presencia ──────────────
        # Gesto largo y tierno: tilt sube lento desde donde estaba hasta mirar arriba.
        # OLED hace SORPRENDIDO breve → CURIOSO sostenido (doble toma expresiva).
        startle = (estado == RobotState.PRESENCE and self._sm.startle_active())
        if startle:
            if not self._startle_prev:
                # Primer tick: SORPRENDIDO instantáneo (impacto visual fuerte)
                self._sm._serial.cmd_estado('SORPRENDIDO')
                # Después de 400ms pasamos a CURIOSO sostenido (vía hilo)
                import threading as _th, time as _tm
                def _to_curioso():
                    _tm.sleep(0.4)
                    if self._sm.startle_active():
                        self._sm._serial.cmd_estado('CURIOSO')
                _th.Thread(target=_to_curioso, daemon=True).start()
            self._startle_prev = True
            self._pan_obj  = self._tracker.pan_actual
            self._tilt_obj = WAKE_TILT_PEAK
            self._tracker.set_objetivo(self._pan_obj, self._tilt_obj)
            # max_paso bajo + suavizado alto → movimiento lento (~16°/s)
            self._tracker.actualizar_servo(None, suavizado=0.92,
                                           max_paso_pan=0.5, max_paso_tilt=0.8)
            return
        else:
            if self._startle_prev:
                # Fin del despertar → volver al OLED de PRESENCE (SIGUIENDO lo
                # mandará el main loop con cmd_siguiendo en el próximo frame)
                self._sm._serial.cmd_estado('SIGUIENDO')
            self._startle_prev = False

        # ── Sueño: si Bob lleva >SLEEP_THR_S en IDLE sin nadie, baja la cabeza ─
        if estado == RobotState.IDLE and self._sm.is_asleep() and det is None:
            self._asleep_prev = True
            self._pan_obj  = SLEEP_PAN
            self._tilt_obj = SLEEP_TILT
            self._tracker.set_objetivo(self._pan_obj, self._tilt_obj)
            # Movimiento lento al "acomodarse" para dormir
            self._tracker.actualizar_servo(None, suavizado=0.95,
                                           max_paso_pan=0.6, max_paso_tilt=0.6)
            return
        self._asleep_prev = False

        if estado in (RobotState.IDLE, RobotState.PRESENCE) and det is None:
            self._tick_idle(ahora)
            if estado == RobotState.IDLE:
                self._maybe_girar_idle(ahora)   # pivots de "mirar alrededor"

        elif estado == RobotState.PRESENCE and det is not None:
            self._tick_presence(det, ahora)
            self._maybe_girar_cuerpo(det, ahora)   # gira el cuerpo si el pan se satura

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
        """Movimiento 'aburrido': waypoints aleatorios amplios con pausas largas."""
        if self._en_pausa:
            if ahora < self._wp_hasta:
                self._pan_obj  = self._wp_pan
                self._tilt_obj = self._wp_tilt
                return
            # Pausa terminó → elegir waypoint NUEVO amplio (no microvariación)
            self._en_pausa = False
            self._wp_pan  = _clamp(random.gauss(PAN_HOME,  35), PAN_MIN  + 10, PAN_MAX  - 10)
            # Búsqueda sesgada hacia arriba: centra en IDLE_TILT_CENTER y no baja
            # más allá del nivel (TILT_HOME) → no se queda mirando el piso.
            self._wp_tilt = _clamp(random.gauss(IDLE_TILT_CENTER, IDLE_TILT_SPREAD),
                                   TILT_MIN + 2, TILT_HOME)
        else:
            # En tránsito hacia waypoint — chequear si llegamos
            dist = math.hypot(self._tracker.pan_actual - self._wp_pan,
                              self._tracker.tilt_actual - self._wp_tilt)
            if dist < 3.0:
                # Llegamos → pausa larga (mirando "algo")
                self._wp_hasta = ahora + random.uniform(1.5, 4.0)
                self._en_pausa = True

        self._pan_obj  = self._wp_pan
        self._tilt_obj = self._wp_tilt

        # Micro-gesto de cabeza de una mueca activa (Fase B): nudge suave.
        if ahora < self._mueca_head_until:
            self._pan_obj  = _clamp(self._pan_obj  + self._mueca_pan_off,  PAN_MIN, PAN_MAX)
            self._tilt_obj = _clamp(self._tilt_obj + self._mueca_tilt_off, TILT_MIN, TILT_MAX)

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

        # Eye contact offset: levantar la mirada N grados para mirar a los ojos
        # en vez del centro de la cara (que con keypoint nariz cae en el centro).
        self._pan_obj  = _clamp(pan_real  + self._desvio_pan_off,
                                _PAN_MIN, _PAN_MAX)
        self._tilt_obj = _clamp(tilt_real + self._desvio_tilt_off + EYE_CONTACT_OFFSET_TILT,
                                _TILT_MIN, _TILT_MAX)

        # Actualizar los objetivos internos del tracker (necesario para el cálculo de siguiente tick)
        self._tracker.pan_obj  = pan_real
        self._tracker.tilt_obj = tilt_real

    def _tick_thinking(self) -> None:
        """
        Pensar: cabeza COMPLETAMENTE quieta en la pose actual.
        Sin micro-inclinación porque movió la cámara y perdía el rostro
        durante el procesamiento STT/LLM.
        """
        if not self._thinking_prev:
            # Primer tick: congelar la pose exactamente donde estaba
            self._pan_obj  = self._tracker.pan_actual
            self._tilt_obj = self._tracker.tilt_actual
            self._thinking_prev = True
        # Resto de los ticks: no tocar nada → servo se mantiene

    def _tick_speaking(self, det, ahora: float) -> None:
        """Tracking lento + desvíos más frecuentes."""
        if det is not None:
            self._tick_presence(det, ahora)
        else:
            self._pan_obj  = self._tracker.pan_actual
            self._tilt_obj = self._tracker.tilt_actual


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v
