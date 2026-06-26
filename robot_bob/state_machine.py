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
        # Se lee de config.SLEEP_THR_S (fallback 20 s) para que sea tuneable.
        try:
            from config import SLEEP_THR_S as _SLEEP_THR
        except Exception:
            _SLEEP_THR = 20.0
        self.SLEEP_THR_S      = float(_SLEEP_THR)
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

        # Escaneo: 'buscar' | 'derecha' | 'izquierda' | None. Lo pone el wake (sin
        # cara) o un comando de voz; lo consume y ejecuta BehaviorEngine.
        self.scan_request: str | None = None
        self._cara_visible: bool = False

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

        # ── Guard de OLED expresivo (featuresAction Fase B) ────────────────
        # Soliloquio y muecas escriben el OLED por su cuenta; este guard evita
        # que se pisen. Flag = ocupado explícito (soliloquio); timestamp =
        # ocupado con auto-expiración (muecas, que conocen su duración).
        self._oled_busy_flag  = False
        self._oled_busy_until = 0.0

        # ── Estados internos (P3 — personalidad que deriva) ────────────────
        # Variables long-lived [0..1] que derivan durante TODA la sesión (a
        # diferencia del mood, que resetea por conversación). Sesgan la iniciativa
        # de Bob y se inyectan al prompt del LLM para que su tono cambie con el
        # tiempo. Se derivan en tick_estados_internos() y se empujan con eventos.
        self._ei_lock     = threading.Lock()
        self.energia      = 0.7   # se gasta hablando, se recupera en reposo/sueño
        self.motivacion   = 0.5   # sube con charlas buenas, baja con rechazo
        self.curiosidad   = 0.5   # sube con caras nuevas, baja al saciarse charlando
        self.sociabilidad = 0.5   # sube tras charlar, baja al estar solo mucho rato
        self._ei_baseline = {'motivacion': 0.5, 'curiosidad': 0.5, 'sociabilidad': 0.5}
        self._t_ei_tick   = time.monotonic()

    # ── Propiedades ────────────────────────────────────────────────────────────

    @property
    def estado(self) -> RobotState:
        with self._lock:
            return self._estado

    @property
    def t_en_estado(self) -> float:
        """Segundos transcurridos desde la última transición de estado."""
        with self._lock:
            return time.monotonic() - self._t_cambio

    @property
    def en_conversacion(self) -> bool:
        with self._lock:
            return self._estado in (
                RobotState.LISTENING,
                RobotState.THINKING,
                RobotState.SPEAKING,
                RobotState.CONVERSATION_IDLE,
            )

    def oled_ocupar(self, ms: float = 0.0) -> None:
        """Marca el OLED como ocupado por una animación expresiva.
        ms>0 → ocupado con auto-expiración (muecas). ms=0 → flag explícito (soliloquio)."""
        if ms > 0:
            self._oled_busy_until = max(self._oled_busy_until,
                                        time.monotonic() + ms / 1000.0)
        else:
            self._oled_busy_flag = True

    def oled_liberar(self) -> None:
        self._oled_busy_flag = False

    def oled_ocupado(self) -> bool:
        return self._oled_busy_flag or (time.monotonic() < self._oled_busy_until)

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

    # ── Estados internos (P3) ──────────────────────────────────────────────────

    @staticmethod
    def _clamp01(x: float) -> float:
        return max(0.0, min(1.0, x))

    def tick_estados_internos(self) -> None:
        """
        Deriva los estados internos según el tiempo y el estado actual. Barato;
        llamar desde el loop principal en cada frame (se auto-throttlea a ~4 Hz).
        """
        ahora = time.monotonic()
        dt = ahora - self._t_ei_tick
        if dt < 0.25:
            return
        self._t_ei_tick = ahora
        with self._lock:
            est = self._estado
        dormido = self.is_asleep()
        with self._ei_lock:
            # Energía: se gasta conversando, se recupera en reposo (más si duerme).
            if est in (RobotState.LISTENING, RobotState.THINKING, RobotState.SPEAKING):
                self.energia -= 0.010 * dt
            elif dormido:
                self.energia += 0.020 * dt
            elif est == RobotState.IDLE:
                self.energia += 0.008 * dt
            self.energia = self._clamp01(self.energia)

            # Solo mucho rato (IDLE) → baja sociabilidad por debajo del baseline.
            if est == RobotState.IDLE:
                self.sociabilidad -= 0.004 * dt

            # Deriva lenta de motivación/curiosidad/sociabilidad hacia su baseline.
            for k in ('motivacion', 'curiosidad', 'sociabilidad'):
                v = getattr(self, k)
                base = self._ei_baseline[k]
                setattr(self, k, self._clamp01(v + (base - v) * min(1.0, 0.02 * dt)))

    def ei_evento_charla(self, turnos: int, mood: float) -> None:
        """Al cerrar una charla: ajusta los estados según cómo fue."""
        with self._ei_lock:
            self.sociabilidad = self._clamp01(self.sociabilidad + 0.10 + 0.05 * max(0.0, mood))
            self.motivacion   = self._clamp01(self.motivacion + 0.08 * mood + 0.02 * min(turnos, 5))
            self.curiosidad   = self._clamp01(self.curiosidad - 0.10)        # saciada
            self.energia      = self._clamp01(self.energia - 0.05 - 0.01 * min(turnos, 8))

    def factor_iniciativa(self) -> float:
        """
        Escala la probabilidad de iniciar charla según el ánimo interno. ~[0.2..1.5]:
        motivado + sociable + con energía → más lanzado; cansado/desganado → reservado.
        """
        with self._ei_lock:
            return max(0.2, 0.5 + 0.4 * self.motivacion + 0.4 * self.sociabilidad
                       + 0.4 * (self.energia - 0.5))

    def estado_interno_prompt(self) -> str:
        """Una línea para el system prompt del LLM: cómo se siente Bob ahora.
        Solo menciona los estados marcadamente altos o bajos (resto = neutro)."""
        def nivel(v, alto, bajo):
            return alto if v >= 0.66 else (bajo if v <= 0.33 else None)
        with self._ei_lock:
            partes = [p for p in (
                nivel(self.energia,      'con mucha energía',       'algo cansado'),
                nivel(self.motivacion,   'muy motivado',            'desganado'),
                nivel(self.curiosidad,   'muy curioso',             'poco curioso'),
                nivel(self.sociabilidad, 'con ganas de socializar', 'reservado'),
            ) if p]
        if not partes:
            return ''
        return ('\n\n═══ TU ESTADO INTERNO AHORA ═══\nAhora mismo te sentís '
                + ', '.join(partes) + '. Que se note sutilmente en tu tono y energía, '
                'sin mencionarlo explícitamente.')

    def estados_internos(self) -> dict:
        """Snapshot para HUD/tests."""
        with self._ei_lock:
            return {'energia': self.energia, 'motivacion': self.motivacion,
                    'curiosidad': self.curiosidad, 'sociabilidad': self.sociabilidad}

    # ── Transiciones (llamadas desde cualquier hilo) ───────────────────────────

    def notificar_cara(self, detectada: bool) -> None:
        """Llamar en cada frame con True si hay cara, False si no."""
        ahora = time.monotonic()
        self._cara_visible = detectada
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
            # Oyó "Bob" pero no ve a nadie → buscar al que habla girando.
            if not self._cara_visible:
                self.scan_request = 'buscar'
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
            # P3: la probabilidad base se escala por el ánimo interno de Bob.
            if random.random() < self._p_conv * self.factor_iniciativa():
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
            # P3: alguien apareció → sube la curiosidad de Bob.
            with self._ei_lock:
                self.curiosidad = self._clamp01(self.curiosidad + 0.15)
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
