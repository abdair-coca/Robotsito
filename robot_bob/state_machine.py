"""
state_machine.py — Máquina de estados del robot Bob.

Estados:
  IDLE              → sin cara, movimientos aleatorios "aburridos"
  PRESENCE          → cara detectada, timer de permanencia activo
  LISTENING         → grabando audio (activado por timer+random o wake word)
  THINKING          → STT + LLM procesando
  SPEAKING          → reproduciendo TTS
  CONVERSATION_IDLE → entre turnos de conversación, timeout de 5 s

Transiciones controladas por threading.Events para sincronización entre hilos.
"""

import enum
import threading
import time
import random


class RobotState(enum.Enum):
    IDLE              = 'IDLE'
    PRESENCE          = 'PRESENCE'
    LISTENING         = 'LISTENING'
    THINKING          = 'THINKING'
    SPEAKING          = 'SPEAKING'
    CONVERSATION_IDLE = 'CONVERSATION_IDLE'


# Mapa estado → comando OLED enviado al ESP32
_OLED_STATE: dict[RobotState, str] = {
    RobotState.IDLE:              'ESPERANDO',
    RobotState.PRESENCE:          'SIGUIENDO',   # manejado por FacialTracker
    RobotState.LISTENING:         'ESCUCHANDO',
    RobotState.THINKING:          'PENSANDO',
    RobotState.SPEAKING:          'HABLANDO',
    RobotState.CONVERSATION_IDLE: 'ESPERANDO',
}


class StateMachine:
    """
    Parámetros de trigger de conversación:
      t_permanencia_min: segundos de cara visible antes de evaluar prob.
      p_conversacion:    probabilidad de iniciar cuando se cumple el timer.
      cooldown_conv:     segundos mínimos entre conversaciones.
      conversation_timeout: segundos de inactividad en CONVERSATION_IDLE para volver a IDLE.
    """

    def __init__(self, serial_mgr,
                 t_permanencia_min: float = 3.0,
                 p_conversacion: float = 0.35,
                 cooldown_conv: float = 30.0,
                 conversation_timeout: float = 5.0,
                 timeout_rostro: float = 1.5):
        self._serial          = serial_mgr
        self._t_perm_min      = t_permanencia_min
        self._p_conv          = p_conversacion
        self._cooldown_conv   = cooldown_conv
        self._conv_timeout    = conversation_timeout
        self._timeout_rostro  = timeout_rostro

        self._lock            = threading.Lock()
        self._estado          = RobotState.IDLE
        self._t_cambio        = time.monotonic()

        # Timestamps relevantes
        self._t_cara_vista    = 0.0   # primera vez que se vio la cara actual
        self._t_cara_perdida  = 0.0   # última vez que se perdió la cara
        self._t_ultima_conv   = 0.0   # última vez que se inició conversación
        self._t_ultimo_turno  = 0.0   # último turno de conversación (para CONVERSATION_IDLE)
        self._t_sin_presencia = time.monotonic()  # cuándo entramos al estado IDLE actual
        self._startle_hasta   = 0.0   # hasta cuándo está activo el gesto de "doble toma"

        # Umbral para considerar a Bob "dormido" (cabeza baja, sin movimiento) y
        # para gatillar el "despertar" cuando aparece una cara nueva.
        # Coincide aproximadamente con el momento en que el firmware del OLED
        # ya tiene el párpado bien bajo (~20 s de ESPERANDO continuo).
        self.SLEEP_THR_S      = 20.0
        self.STARTLE_DUR_S    = 1.5  # duración del gesto de despertar (lento + CURIOSO)
        # Alias retrocompatible con código que aún use STARTLE_THR_S
        self.STARTLE_THR_S    = self.SLEEP_THR_S

        # Eventos para sincronización de hilos
        self.ev_escuchando  = threading.Event()  # VoicePipeline: empieza a grabar
        self.ev_pensando    = threading.Event()  # VoicePipeline: empieza STT/LLM
        self.ev_hablando    = threading.Event()  # VoicePipeline: empieza TTS
        self.ev_idle_conv   = threading.Event()  # Esperando siguiente turno
        self.ev_idle        = threading.Event()  # Volvió a IDLE completo

        # Pending audio del barge-in (bytes | None)
        self.pending_audio: bytes | None = None
        # Pending text del wake monitor (frase capturada junto al wake word)
        self.pending_text: str | None = None
        # Flag: la próxima conversación debe arrancar con saludo divertido
        self.greeting_pending: bool = False
        # Origen del trigger: 'wake' (usuario dijo Bob) o 'auto' (Bob inició solo)
        # Determina qué lista de frases usar en voice_pipeline.
        self.conversation_trigger: str | None = None

        # ── Mood drift (InteractiveGoal Fase A) ────────────────────────────
        # Estado de ánimo acumulado DENTRO de la conversación actual.
        # Rango [-1.0, +1.0]. Se resetea al volver a IDLE.
        self.mood: float = 0.0
        self._mood_lock = threading.Lock()

        # ── Continuidad (InteractiveGoal Fase C) ───────────────────────────
        # Turnos positivos consecutivos → si llega a 3, Bob arranca eufórico.
        self.positive_streak: int = 0
        # Fallos de STT consecutivos → a los 2, mensaje empático y reintento.
        self.stt_fail_streak: int = 0

    # ── Propiedades ────────────────────────────────────────────────────────────

    @property
    def estado(self) -> RobotState:
        with self._lock:
            return self._estado

    @property
    def en_conversacion(self) -> bool:
        with self._lock:
            return self._estado in (
                RobotState.LISTENING,
                RobotState.THINKING,
                RobotState.SPEAKING,
                RobotState.CONVERSATION_IDLE,
            )

    def startle_active(self) -> bool:
        """True si está corriendo el gesto de despertar (sorpresa lenta + CURIOSO)."""
        return time.monotonic() < self._startle_hasta

    def is_asleep(self) -> bool:
        """
        True si Bob lleva más de SLEEP_THR_S sin presencia en IDLE → cabeza
        debe quedar quieta (postura de sueño). Coordina con el OLED sleepy.
        """
        with self._lock:
            if self._estado != RobotState.IDLE:
                return False
        return (time.monotonic() - self._t_sin_presencia) > self.SLEEP_THR_S

    # ── Mood drift (InteractiveGoal Fase A) ────────────────────────────────────

    def mood_event(self, delta: float) -> None:
        """Suma delta al mood, clampeado a [-1, +1]."""
        with self._mood_lock:
            self.mood = max(-1.0, min(1.0, self.mood + delta))

    def mood_decay(self) -> None:
        """Acerca el mood 5% hacia 0 (llamar una vez por turno)."""
        with self._mood_lock:
            self.mood *= 0.95

    def mood_reset(self) -> None:
        """Vuelve al baseline. Llamar cuando la conversación termina de verdad."""
        with self._mood_lock:
            self.mood = 0.0
        self.positive_streak = 0
        self.stt_fail_streak = 0

    def mood_floor(self, value: float) -> None:
        """Sube el mood a un mínimo (e.g. cariño directo → salto a +0.6)."""
        with self._mood_lock:
            if self.mood < value:
                self.mood = value

    # ── Transiciones (llamadas desde cualquier hilo) ───────────────────────────

    def notificar_cara(self, detectada: bool) -> None:
        """Llamar en cada frame con True si hay cara, False si no."""
        ahora = time.monotonic()
        with self._lock:
            estado = self._estado

        if detectada:
            if self._t_cara_vista == 0.0:
                self._t_cara_vista = ahora
            self._t_cara_perdida = 0.0

            if estado == RobotState.IDLE:
                self._transicionar(RobotState.PRESENCE)
            elif estado == RobotState.PRESENCE:
                self._evaluar_inicio_conv(ahora)
        else:
            # Sin cara. NO reseteamos t_cara_vista en cada frame: flickers cortos
            # de MediaPipe nunca dejarían acumular permanencia. Solo reseteamos
            # al transicionar realmente a IDLE (tras timeout_rostro).
            if self._t_cara_perdida == 0.0:
                self._t_cara_perdida = ahora

            tiempo_sin_cara = ahora - self._t_cara_perdida
            if estado == RobotState.PRESENCE and tiempo_sin_cara > self._timeout_rostro:
                self._t_cara_vista = 0.0
                self._transicionar(RobotState.IDLE)

    def notificar_wake_word(self, payload: str | None = None) -> None:
        """
        Wake word detectado — forzar inicio de conversación.
        payload: texto capturado después del wake (e.g. "como estas" tras "bob como estas").
                 Si está, se pasa al pipeline como primer turno sin re-grabar.
        """
        with self._lock:
            estado = self._estado
        if estado in (RobotState.IDLE, RobotState.PRESENCE):
            self.greeting_pending = True
            self.conversation_trigger = 'wake'
            self.pending_text = (payload or '').strip() or None
            self._transicionar(RobotState.LISTENING)

    def iniciar_escuchando(self) -> None:
        self._transicionar(RobotState.LISTENING)

    def iniciar_pensando(self) -> None:
        self._transicionar(RobotState.THINKING)

    def iniciar_hablando(self) -> None:
        self._transicionar(RobotState.SPEAKING)

    def fin_turno(self, pending: bytes | None = None) -> None:
        """Llamar cuando el robot termina de hablar."""
        self.pending_audio = pending
        self._t_ultimo_turno = time.monotonic()
        self._transicionar(RobotState.CONVERSATION_IDLE)

    def tick_conversation_idle(self) -> bool:
        """
        Llamar periódicamente desde VoicePipeline en estado CONVERSATION_IDLE.
        Devuelve True si debe iniciar otro turno, False si timeout → vuelve a IDLE.
        """
        ahora = time.monotonic()
        with self._lock:
            if self._estado != RobotState.CONVERSATION_IDLE:
                return False
        if ahora - self._t_ultimo_turno > self._conv_timeout:
            self._t_ultima_conv = ahora  # cooldown sigue corriendo
            self.mood_reset()            # la conversación terminó → baseline
            self._transicionar(RobotState.IDLE)
            return False
        return True  # sigue en conversación

    # ── Interno ────────────────────────────────────────────────────────────────

    def _evaluar_inicio_conv(self, ahora: float) -> None:
        if self._t_cara_vista == 0.0:
            return
        tiempo_cara = ahora - self._t_cara_vista
        cooldown_ok = (ahora - self._t_ultima_conv) > self._cooldown_conv
        if tiempo_cara >= self._t_perm_min and cooldown_ok:
            if random.random() < self._p_conv:
                self._t_cara_vista = 0.0  # resetear para no re-disparar
                self._t_ultima_conv = ahora
                # Bob inicia solo → opener divertido + ningún payload pendiente
                self.greeting_pending = True
                self.conversation_trigger = 'auto'
                self.pending_text = None
                self._transicionar_locked(RobotState.LISTENING)

    def _transicionar(self, nuevo: RobotState) -> None:
        with self._lock:
            self._transicionar_locked(nuevo)

    def _transicionar_locked(self, nuevo: RobotState) -> None:
        if self._estado == nuevo:
            return
        anterior = self._estado
        self._estado   = nuevo
        self._t_cambio = time.monotonic()

        # "Despertar": si pasamos de IDLE a PRESENCE tras >SLEEP_THR_S sin presencia,
        # activamos el gesto de despertar lento + CURIOSO por STARTLE_DUR_S.
        if anterior == RobotState.IDLE and nuevo == RobotState.PRESENCE:
            ausencia = self._t_cambio - self._t_sin_presencia
            if ausencia > self.SLEEP_THR_S:
                self._startle_hasta = self._t_cambio + self.STARTLE_DUR_S
        # Marcar inicio de ausencia cuando entramos a IDLE
        if nuevo == RobotState.IDLE:
            self._t_sin_presencia = self._t_cambio

        # Notificar OLED (no durante PRESENCE — FacialTracker manda SIGUIENDO)
        if nuevo != RobotState.PRESENCE:
            oled_cmd = _OLED_STATE.get(nuevo)
            if oled_cmd:
                self._serial.cmd_estado(oled_cmd)

        # Despertar eventos
        self.ev_escuchando.clear()
        self.ev_pensando.clear()
        self.ev_hablando.clear()
        self.ev_idle_conv.clear()
        self.ev_idle.clear()

        if nuevo == RobotState.LISTENING:
            self.ev_escuchando.set()
        elif nuevo == RobotState.THINKING:
            self.ev_pensando.set()
        elif nuevo == RobotState.SPEAKING:
            self.ev_hablando.set()
        elif nuevo == RobotState.CONVERSATION_IDLE:
            self.ev_idle_conv.set()
        elif nuevo == RobotState.IDLE:
            self.ev_idle.set()
