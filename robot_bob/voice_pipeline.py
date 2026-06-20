"""
voice_pipeline.py — Pipeline de voz del robot Bob.

Adapta chat.py para integrarse con StateMachine:
  - run() corre en su propio hilo, respeta ev_escuchando para saber cuándo grabar
  - Usa SerialManager en vez de RobotSerial (COM3 ya no se abre aquí)
  - Notifica StateMachine en cada fase: LISTENING → THINKING → SPEAKING → CONV_IDLE
  - Mantiene WakeWordDetector activo siempre (wake word = override en cualquier estado)

Dependencias externas: voicechatLap/config.py y voicechatLap/wake_word.py
(se importan con ruta relativa; ajustar sys.path en main.py si es necesario)
"""

from __future__ import annotations

import io as _io
import os
import random
import re
import sys
import asyncio
import queue
import subprocess
import tempfile
import threading
import time
import wave
from datetime import datetime
from collections import deque
from typing import Optional, List, Dict, Iterator

import numpy as np
import webrtcvad
import edge_tts
import imageio_ffmpeg
import sounddevice as sd
from scipy.signal import butter, sosfilt
from groq import Groq
from rich.console import Console

# voicechatLap se appendea (no se inserta al frente) para que robot_bob/config.py
# tenga prioridad sobre voicechatLap/config.py al hacer `from config import ...`.
# Esto deja que Abdair edite robot_bob/config.py como la única fuente de verdad
# y que wake_word.py y audio_io.py sigan siendo importables.
_VOICECHAT_DIR = os.path.join(os.path.dirname(__file__), '..', 'voicechatLap')
if _VOICECHAT_DIR not in sys.path:
    sys.path.append(os.path.abspath(_VOICECHAT_DIR))

from config import (
    GROQ_API_KEY,
    USE_ROBOT_SPEAKER, USE_ROBOT_MIC,
    SAMPLE_RATE, FRAME_MS, FRAME_SIZE, MIC_CHUNK_BYTES,
    VAD_AGGRESSIVENESS, SILENCE_END_MS, MAX_RECORDING_S, MIN_SPEECH_S,
    PREROLL_FRAMES, NOISE_FLOOR_INIT, NOISE_FLOOR_MARGIN, NOISE_FLOOR_MIN,
    SPEECH_START_FRAMES,
    BARGE_IN_ENABLED, BARGE_IN_SUSTAINED_MS, BARGE_IN_SETTLE_MS, BARGE_IN_RMS_U8,
    WAKE_WORD_ENABLED,
    WAKE_CANONICAL, WAKE_PREFIXES, WAKE_MIN_CONF, WAKE_FUZZY_THR,
    WAKE_COOLDOWN_S, WAKE_MAX_UTTR_CHARS,
    GROQ_LLM_MODEL, GROQ_STT_MODEL, TEMPERATURE, MAX_TOKENS, MAX_RETRIES,
    VOICE, TTS_FFMPEG_FILTERS, SENTENCE_MIN_CHARS, TTS_TAIL_S,
    SYSTEM_PROMPT, EXIT_PHRASES, GOODBYE_PHRASES,
    TTS_SEND_CHUNK_BYTES,
    SOLILOQUIO_ENABLED, SOLO_IDLE_MIN_S, PRESENCE_NUDGE_S,
    P_SOLILOQUIO, SOLILOQUIO_COOLDOWN_S, SOLILOQUIO_SETTLE_MS, BANCO_SOLILOQUIO,
    SOLILOQUIO_USA_LLM, SOLILOQUIO_LLM_RATIO, SOLILOQUIO_LLM_MAX_TOK,
    SOLILOQUIO_MAX_CARNADAS,
)
# Prosodia por emoción (config local, gitignoreado). Fallback neutro si falta.
try:
    from config import TTS_RATE_BASE, TTS_PITCH_BASE, TTS_EMO_PROSODY
except Exception:
    TTS_RATE_BASE, TTS_PITCH_BASE, TTS_EMO_PROSODY = "+0%", "+0Hz", {}

# Gate de energía del wake monitor (silencio → no transcribir).
try:
    from config import WAKE_MIN_LEVEL
except Exception:
    WAKE_MIN_LEVEL = 1.3
from wake_word import WakeWordDetector
from expression_engine import (
    pulse_emotion, react_to_user_text, react_to_bob_text,
    mood_delta_for_user_text, is_love,
    EMO_WAKE_DETECTED, EMO_GREETING_PLAYED, EMO_AUTO_OPENER,
    EMO_STT_FAIL, EMO_LLM_ERROR,
    PULSE_FAST, PULSE_NORM, PULSE_SLOW,
)

# ── Tags de emoción del LLM (InteractiveGoal Fase A) ───────────────────────────
# El LLM prefija cada frase con [EMO:X]. Lo extraemos para el OLED y lo
# quitamos del texto antes del TTS (no debe leerse en voz alta).
_EMO_TAG_RE = re.compile(r'\[EMO:([A-ZÁÉÍÓÚÜÑ_]+)\]', re.IGNORECASE)
_VALID_EMOS = {'FELIZ', 'MUY_FELIZ', 'CURIOSO', 'TRAVIESO', 'PENSANDO',
               'SORPRENDIDO', 'CONFUNDIDO', 'TRISTE', 'AMOR', 'HABLANDO'}


def _extract_emo_tag(text: str) -> tuple:
    """
    Devuelve (emo | None, texto_limpio).
    Usa el primer tag válido encontrado; borra TODOS los tags del texto.
    """
    emo = None
    for m in _EMO_TAG_RE.finditer(text):
        cand = m.group(1).upper()
        if emo is None and cand in _VALID_EMOS:
            emo = cand
    clean = _EMO_TAG_RE.sub('', text).strip()
    return emo, clean

FFMPEG  = imageio_ffmpeg.get_ffmpeg_exe()
console = Console()

# ── Constantes locales ─────────────────────────────────────────────────────────
_LAP_SAMPLE_RATE = 16000  # sounddevice graba a 16 kHz
_SILENCE_FRAMES  = SILENCE_END_MS // FRAME_MS
_MAX_FRAMES      = (MAX_RECORDING_S * 1000) // FRAME_MS

# Saludos rápidos cuando el usuario gatilla wake word ("Bob ...")
WAKE_GREETINGS = [
    '¡Hola bola!',
    '¿Qué pasa calabaza?',
    '¡Aquí estoy!',
    '¡Dime, dime!',
    '¡Sí dime!',
    '¿Qué tranza compadre?',
    '¡Te escucho!',
    '¿Qué hubo qué hay?',
    '¡A la orden!',
    '¿Qué onda banana?',
    '¿Qué tal lechuga?',
    '¡Aquí Bob, reportándose!',
]

# Openers cuando Bob inicia solo (vio una cara permanente, se animó a romper hielo).
# Preguntas abiertas para invitar a charlar — el usuario no esperaba ser hablado.
AUTO_OPENERS = [
    '¡Hola! Soy Bob, ¿y tú cómo te llamas?',
    '¡Buenas! ¿De qué carrera eres?',
    '¡Hey! ¿Qué te trae por la feria?',
    '¡Hola! Te estaba mirando, ¿charlamos?',
    '¡Buenas! ¿Cómo va tu día?',
    '¡Eh, hola! ¿Te cuento un chiste?',
    '¡Hola! ¿Vienes a ver robots o a ver robots?',
    '¡Saludos! ¿Te puedo preguntar algo?',
    '¡Hola! Me aburría, ¿hablamos un rato?',
    '¡Buenas! ¿Sabías que llevo aquí horas? Cuéntame algo.',
    '¡Hola! ¿Eres de Potosí o de visita?',
    '¡Hey! ¿Qué opinas de los robots conversacionales?',
]

# Prompt para soliloquios generados por LLM (Fase C). Pide UNA frase corta,
# hablable, con su tag [EMO:X]. Barato (pocos tokens) y con temperatura alta
# para variedad.
SOLILOQUIO_LLM_PROMPT = (
    "Eres Bob, un robot sociable en una feria de la Universidad Autónoma Tomás "
    "Frías (UATF), en Potosí, Bolivia. Ahora estás SOLO, nadie te habla.\n"
    "Di UNA sola frase corta (máximo 12 palabras) que dirías en voz alta para ti "
    "mismo: un pensamiento, una queja liviana, una curiosidad o una broma con "
    "actitud. Tono de compañero de la facu, boliviano, divertido.\n"
    "La frase DEBE empezar con un tag de emoción entre corchetes: [EMO:FELIZ], "
    "[EMO:CURIOSO], [EMO:TRAVIESO], [EMO:PENSANDO], [EMO:TRISTE], [EMO:MUY_FELIZ], "
    "[EMO:CONFUNDIDO] o [EMO:AMOR].\n"
    "Texto plano hablable: sin comillas, sin markdown, sin emojis, sin acotaciones. "
    "Solo la frase con su tag."
)

# Filtro pasa-banda de voz (pre-calculado, reutilizable)
_VOICE_BANDPASS = butter(4, [80, 3500], btype='band', fs=SAMPLE_RATE, output='sos')


# ── Helpers de audio ───────────────────────────────────────────────────────────

def _rms_uint8(buf: bytes) -> float:
    a = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
    if not a.size:
        return 0.0
    a = a - a.mean()
    return float(np.sqrt(np.mean(a * a)))

def _uint8_to_int16_bytes(buf: bytes) -> bytes:
    a = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
    if not a.size:
        return b''
    a = (a - a.mean()) * 256.0
    return np.clip(a, -32768, 32767).astype(np.int16).tobytes()

def _uint8_to_wav(uint8_audio: bytes) -> bytes:
    arr = np.frombuffer(uint8_audio, dtype=np.uint8).astype(np.float32) - 128.0
    arr -= arr.mean()
    arr = sosfilt(_VOICE_BANDPASS, arr).astype(np.float32)
    peak = np.max(np.abs(arr))
    if peak > 1.0:
        arr = arr / peak * 0.9 * 32767.0
    int16 = arr.astype(np.int16)
    buf = _io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(int16.tobytes())
    return buf.getvalue()

def _is_happy(text: str) -> bool:
    HAPPY_KEYWORDS = {
        'bien', 'genial', 'excelente', 'perfecto', 'fantástico', 'claro', 'por supuesto',
        'feliz', 'alegre', 'encantado', 'maravilloso', 'increíble', 'buenísimo',
    }
    words = set(text.lower().split())
    return bool(words & HAPPY_KEYWORDS)

# Frases que Whisper alucina con silencio/ruido — se ignoran en el wake monitor.
_ALUCINACIONES = {
    'gracias', 'muchas gracias', 'gracias por ver el video', 'gracias por ver',
    'no', 'si', 'ya', 'eh', 'ah', 'mmm', 'chau', 'ok', 'okay', 'a', 'y', 'the',
}

def _es_alucinacion(texto: str) -> bool:
    t = texto.lower().strip(' .,!?¿¡')
    if not t:
        return True
    if 'amara' in t or 'subtitul' in t or 'suscrib' in t:
        return True
    return t in _ALUCINACIONES

# Comandos de voz para que Bob gire buscando al que habla.
_GIRO_DER_KW = ('a la derecha', 'tu derecha', 'a tu derecha', 'por la derecha',
                'hacia la derecha')
_GIRO_IZQ_KW = ('a la izquierda', 'tu izquierda', 'a tu izquierda', 'por la izquierda',
                'hacia la izquierda')
_GIRO_BUSCAR_KW = ('date la vuelta', 'date vuelta', 'da la vuelta', 'date media vuelta',
                   'date la media vuelta', 'voltea', 'volteate', 'voltéate', 'gira',
                   'girate', 'gírate', 'date la vueltita', 'estoy detras', 'estoy detrás',
                   'detras de ti', 'detrás de ti', 'atras de ti', 'atrás de ti',
                   'aca atras', 'acá atrás', 'aqui atras', 'aquí atrás', 'mira atras',
                   'mira atrás', 'date la vue')

def _intent_giro(texto: str):
    """Devuelve 'derecha' | 'izquierda' | 'buscar' | None según el comando de voz."""
    t = texto.lower()
    if any(k in t for k in _GIRO_DER_KW):
        return 'derecha'
    if any(k in t for k in _GIRO_IZQ_KW):
        return 'izquierda'
    if any(k in t for k in _GIRO_BUSCAR_KW):
        return 'buscar'
    return None

def _is_exit(text: str) -> bool:
    return text.lower().strip(' .,!?¿¡') in EXIT_PHRASES

def _is_goodbye(text: str) -> bool:
    return text.lower().strip(' .,!?¿¡') in GOODBYE_PHRASES


# ── TTS ────────────────────────────────────────────────────────────────────────

def _prosody_for_emo(emo: Optional[str]) -> tuple:
    """Devuelve (rate, pitch) edge-tts según la emoción; default = base."""
    if emo:
        p = TTS_EMO_PROSODY.get(emo.upper())
        if p:
            return p
    return (TTS_RATE_BASE, TTS_PITCH_BASE)

async def _edge_tts_bytes(text: str, rate: str, pitch: str) -> bytes:
    chunks = []
    async for c in edge_tts.Communicate(text, voice=VOICE, rate=rate, pitch=pitch).stream():
        if c['type'] == 'audio':
            chunks.append(c['data'])
    return b''.join(chunks)

def _synthesize_mp3(text: str, emo: Optional[str] = None) -> bytes:
    if not text.strip():
        return b''
    rate, pitch = _prosody_for_emo(emo)
    return asyncio.run(_edge_tts_bytes(text, rate, pitch))

def _mp3_to_wav(mp3: bytes) -> bytes:
    proc = subprocess.run(
        [FFMPEG, '-hide_banner', '-loglevel', 'error',
         '-i', 'pipe:0',
         '-af', TTS_FFMPEG_FILTERS,
         '-ar', str(SAMPLE_RATE), '-ac', '1',
         '-acodec', 'pcm_u8', '-f', 'u8', 'pipe:1'],
        input=mp3, capture_output=True, check=False,
    )
    return proc.stdout


# ── Sentence splitter ──────────────────────────────────────────────────────────

_SENT_RE = re.compile(r'[.!?¡¿…\n]+[\s"\')\]]*')

def _split_sentence(buf: str) -> tuple[Optional[str], str]:
    if len(buf) < SENTENCE_MIN_CHARS:
        return None, buf
    m = _SENT_RE.search(buf, SENTENCE_MIN_CHARS - 1)
    if m is None:
        return None, buf
    return buf[:m.end()].strip(), buf[m.end():]


# ── VoicePipeline ──────────────────────────────────────────────────────────────

class VoicePipeline:
    """
    Corre en un hilo daemon. Espera ev_escuchando de StateMachine para grabar.
    Flujo por turno: graba → transcribe → detecta wake/exit → LLM → TTS → reproduce.

    serial_mgr: SerialManager  (para comandos ESTADO sin abrir COM3)
    state_machine: StateMachine (para notificar transiciones)
    """

    def __init__(self, serial_mgr, state_machine, audio_io=None):
        """
        audio_io: instancia opcional de AudioIO (voicechatLap.audio_io) ya conectada.
                  Si USE_ROBOT_MIC o USE_ROBOT_SPEAKER son True y audio_io está,
                  se rutea el audio por TCP al ESP32 en lugar de laptop.
        """
        self._serial = serial_mgr
        self._sm     = state_machine
        self._audio_io = audio_io

        self._client = Groq(api_key=GROQ_API_KEY)
        self._vad    = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self._convo: List[Dict[str, str]] = []
        self._detener = threading.Event()
        # Anti-eco: cuando Bob habla solo (soliloquio), el wake monitor NO debe
        # grabar su propia voz. Este flag lo silencia mientras dura el soliloquio.
        self._muted = threading.Event()
        # Anti-repetición de soliloquios: últimas frases dichas (texto sin tag).
        self._soliloquio_reciente: deque = deque(maxlen=5)

        self._wake = WakeWordDetector(
            wake_word=WAKE_CANONICAL,
            prefixes=WAKE_PREFIXES,
            min_confidence=WAKE_MIN_CONF,
            fuzzy_threshold=WAKE_FUZZY_THR,
            cooldown_s=WAKE_COOLDOWN_S,
            max_utterance_chars=WAKE_MAX_UTTR_CHARS,
        )

        # Pygame para reproducción local
        import pygame
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)
        except Exception as e:
            console.print(f'[red][voice] pygame error: {e}[/]')
        self._pygame = pygame

        self._warmup()
        self._hilo = threading.Thread(target=self._loop, daemon=True, name='voice-pipeline')
        self._hilo.start()

    # ── API pública ────────────────────────────────────────────────────────────

    def cerrar(self) -> None:
        self._detener.set()
        self._sm.ev_escuchando.set()  # desbloquea el wait
        self._hilo.join(timeout=3.0)

    # ── Loop principal ────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while not self._detener.is_set():
            # Esperar hasta que StateMachine diga LISTENING
            self._sm.ev_escuchando.wait(timeout=0.5)
            if self._detener.is_set():
                break
            if not self._sm.ev_escuchando.is_set():
                continue

            # Capturar flags pendientes ANTES de arrancar la conversación
            pending_audio = self._sm.pending_audio
            pending_text  = self._sm.pending_text
            greeting      = self._sm.greeting_pending
            trigger       = self._sm.conversation_trigger
            self._sm.pending_audio        = None
            self._sm.pending_text         = None
            self._sm.greeting_pending     = False
            self._sm.conversation_trigger = None

            self._run_conversation(pending_audio=pending_audio,
                                   pending_text=pending_text,
                                   greeting=greeting,
                                   trigger=trigger)

    def _run_conversation(self, pending_audio: Optional[bytes] = None,
                          pending_text: Optional[str] = None,
                          greeting: bool = False,
                          trigger: Optional[str] = None) -> None:
        """
        Maneja una conversación completa: turno inicial + turnos sucesivos
        sin requerir wake word entre ellos. Termina cuando:
          - El usuario no responde en NEXT_TURN_TIMEOUT segundos
          - El usuario dice una frase de salida/despedida
          - Se llama a cerrar()

        pending_text: si está presente, se usa como primer turno SIN grabar/transcribir
                      (caso típico: "Bob ¿cómo estás?" — el payload tras el wake).
        greeting:     si True, dice un saludo random ANTES de proceder.
        """
        NEXT_TURN_TIMEOUT = 6.0   # s para esperar respuesta del usuario tras hablar Bob
        FIRST_TURN_TIMEOUT = 10.0  # s para el primer turno tras wake word

        es_primer_turno = True
        n_turnos = 0  # contador de turnos para el bonus de mood (charla fluye)

        # ── Saludo / opener al arrancar conversación ──────────────────────────
        if greeting:
            if trigger == 'auto':
                saludo = random.choice(AUTO_OPENERS)
                console.print(f'[bold magenta][opener auto][/] {saludo}')
                pulse_emotion(self._serial, self._sm, EMO_AUTO_OPENER, PULSE_FAST)
            else:
                saludo = random.choice(WAKE_GREETINGS)
                console.print(f'[bold magenta][saludo wake][/] {saludo}')
                pulse_emotion(self._serial, self._sm, EMO_GREETING_PLAYED, PULSE_FAST)
            self._sm.iniciar_hablando()
            self._hablar(saludo)
            # Si no hay payload pendiente, volvemos a LISTENING para grabar
            if not pending_text:
                self._sm.iniciar_escuchando()

        while not self._detener.is_set():
            # ── 1. Obtener input del turno ─────────────────────────────────────
            texto: str = ''
            if pending_text:
                # El wake monitor ya transcribió la frase entera junto al wake.
                # Saltamos grabar + STT para este primer turno.
                texto = pending_text
                pending_text = None
                console.print(f'[dim][voice] Usando payload del wake: "{texto}"[/]')
                self._sm.iniciar_pensando()
            elif pending_audio:
                audio = pending_audio
                pending_audio = None
                console.print('[dim][voice] Usando audio de barge-in[/]')
                self._sm.iniciar_pensando()
                console.print('[voice] Transcribiendo...')
                texto = self._transcribir(audio)
            else:
                if not es_primer_turno:
                    self._sm.iniciar_escuchando()
                timeout = FIRST_TURN_TIMEOUT if es_primer_turno else NEXT_TURN_TIMEOUT
                console.print(f'[voice] Escuchando ({timeout:.0f}s timeout)...')
                audio = self._grabar(initial_timeout=timeout)
                if audio is None or not audio:
                    console.print('[dim][voice] Silencio prolongado, fin de conversación[/]')
                    break
                self._sm.iniciar_pensando()
                console.print('[voice] Transcribiendo...')
                texto = self._transcribir(audio)

            if not texto:
                console.print('[dim][voice] Audio sin texto[/]')
                # Pulso CONFUNDIDO para señalar visualmente que no entendió
                pulse_emotion(self._serial, self._sm, EMO_STT_FAIL, PULSE_FAST)
                self._sm.mood_event(-0.05)  # silencio incomprendido
                self._sm.stt_fail_streak += 1
                if self._sm.stt_fail_streak >= 2:
                    # Fase C: 2 fallos seguidos → Bob lo reconoce con empatía
                    # y da otra oportunidad en vez de cortar la conversación.
                    self._sm.stt_fail_streak = 0
                    console.print('[dim][voice] 2 fallos STT → mensaje empático[/]')
                    self._sm.iniciar_hablando()
                    self._serial.cmd_estado('CONFUNDIDO')
                    self._hablar('Perdón, no te estoy escuchando bien. '
                                 'Acércate un poquito y dime de nuevo, ¿sí?', 'CONFUNDIDO')
                    es_primer_turno = False
                    continue
                break

            console.print(f'[bold cyan]Usuario:[/] {texto}')

            # Reaccionar emocionalmente a lo que dijo el usuario
            # (AMOR / TRISTE / CONFUNDIDO según contenido)
            react_to_user_text(self._serial, self._sm, texto)

            # ── Mood drift (Fase A) + continuidad (Fase C) ─────────────────
            self._sm.stt_fail_streak = 0   # transcripción exitosa
            self._sm.mood_decay()                                  # 5% hacia 0
            delta = mood_delta_for_user_text(texto)
            self._sm.mood_event(delta)
            if is_love(texto):
                self._sm.mood_floor(0.6)   # cariño directo: salto de ánimo
            if delta > 0:
                self._sm.positive_streak += 1
            elif delta < 0:
                self._sm.positive_streak = 0
            n_turnos += 1
            if n_turnos > 4:
                self._sm.mood_event(0.10)  # la conversación fluye — bonus
            console.print(f'[dim][mood] {self._sm.mood:+.2f}  '
                          f'racha+{self._sm.positive_streak}[/]')

            # Limpiar wake word residual si el usuario repitió "Bob"
            ww = self._wake.detect(texto)
            if ww.detected and ww.payload:
                texto = ww.payload

            if not texto.strip():
                es_primer_turno = False
                continue

            # ── Comando de giro: "date la vuelta", "a tu derecha", etc. ─────────
            intent = _intent_giro(texto)
            if intent:
                self._sm.scan_request = intent
                self._sm.iniciar_hablando()
                ack = random.choice(['¡Voy para allá!', '¡Ya te busco!',
                                     '¡Me doy la vuelta!', '¡A ver dónde estás!'])
                console.print(f'[bold green]Bob[/] [dim](giro:{intent})[/]: {ack}')
                self._hablar(ack, 'TRAVIESO')
                es_primer_turno = False
                continue

            # ── 4. Comandos de salida (cualquier turno) ────────────────────────
            if _is_exit(texto):
                self._hablar('Hasta luego.')
                break
            if _is_goodbye(texto):
                self._hablar('Hasta pronto.')
                break

            # ── 5. LLM + TTS paralelo ──────────────────────────────────────────
            console.print('[voice] Pensando...')
            captured = self._stream_and_speak(texto)

            # Si hubo barge-in, el audio capturado va al siguiente turno
            pending_audio = captured
            es_primer_turno = False

        # ── 6. Fin de conversación ─────────────────────────────────────────────
        self._sm.fin_turno()

    # ── Grabación ────────────────────────────────────────────────────────────

    def _grabar(self, initial_timeout: float = 8.0) -> Optional[bytes]:
        """
        Graba con VAD. Despacha a robot (ESP32 TCP) o laptop (sounddevice)
        según el flag USE_ROBOT_MIC y la disponibilidad del AudioIO.
        """
        if USE_ROBOT_MIC and self._audio_io is not None and self._audio_io.connected:
            return self._grabar_robot(initial_timeout)
        return self._grabar_laptop(initial_timeout)

    def _grabar_robot(self, initial_timeout: float) -> Optional[bytes]:
        """
        Graba uint8 @ 8 kHz desde el mic del ESP32 vía AudioIO.
        Misma lógica de VAD + endpointing que la versión laptop.
        """
        self._audio_io.drain_mic()  # tirar audio viejo
        vad_local   = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        u8_per_frame = SAMPLE_RATE * FRAME_MS // 1000  # 240 bytes / frame VAD a 8 kHz

        preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        recorded: List[bytes] = []
        buffer = bytearray()
        speech_started = False
        silence_streak = 0
        speech_ms      = 0
        waited_ms      = 0
        initial_timeout_ms = int(initial_timeout * 1000)

        while True:
            chunk = self._audio_io.get_mic(timeout=0.5)
            if chunk is None:
                waited_ms += 500
                if not speech_started and waited_ms > initial_timeout_ms:
                    return None
                continue
            buffer.extend(chunk)

            # Slice frames VAD de tamaño fijo
            while len(buffer) >= u8_per_frame:
                frame_u8 = bytes(buffer[:u8_per_frame])
                del buffer[:u8_per_frame]

                # webrtcvad requiere int16 → convertir
                frame_i16 = _uint8_to_int16_bytes(frame_u8)
                try:
                    is_vad = vad_local.is_speech(frame_i16, SAMPLE_RATE)
                except Exception:
                    is_vad = False

                rms = _rms_uint8(frame_u8)

                if not speech_started:
                    preroll.append(frame_u8)
                    waited_ms += FRAME_MS
                    if is_vad and rms > NOISE_FLOOR_MIN:
                        speech_started = True
                        recorded.extend(preroll)
                        speech_ms = FRAME_MS * len(preroll)
                    elif waited_ms > initial_timeout_ms:
                        return None
                else:
                    recorded.append(frame_u8)
                    speech_ms += FRAME_MS
                    if is_vad and rms > NOISE_FLOOR_MIN:
                        silence_streak = 0
                    else:
                        silence_streak += 1
                    if silence_streak >= _SILENCE_FRAMES:
                        if speech_ms < MIN_SPEECH_S * 1000:
                            return None
                        raw = b''.join(recorded)
                        return _uint8_to_wav(raw)
                    if speech_ms >= MAX_RECORDING_S * 1000:
                        raw = b''.join(recorded)
                        return _uint8_to_wav(raw)

    def _grabar_laptop(self, initial_timeout: float = 8.0) -> Optional[bytes]:
        """
        Graba desde micrófono de laptop con VAD + gate adaptativo.
        initial_timeout: segundos máximo de silencio inicial antes de devolver None.
        """
        q: queue.Queue = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(bytes(indata))

        frame_bytes = int(_LAP_SAMPLE_RATE * FRAME_MS / 1000) * 2  # int16
        vad_local   = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        initial_timeout_ms = int(initial_timeout * 1000)

        preroll:  deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        recorded: List[bytes]  = []
        speech_started = False
        silence_streak = 0
        speech_ms      = 0
        waited_ms      = 0
        invite_sent    = False  # Fase D: CURIOSO de invitación a los 3 s

        with sd.RawInputStream(samplerate=_LAP_SAMPLE_RATE, channels=1,
                               dtype='int16', blocksize=frame_bytes // 2,
                               callback=callback):
            while True:
                try:
                    chunk = q.get(timeout=1.0)
                except queue.Empty:
                    chunk = None

                if chunk is None:
                    waited_ms += 1000
                    if not speech_started and waited_ms > initial_timeout_ms:
                        return None
                    continue

                # El VAD de webrtcvad necesita frames de 10/20/30 ms exactos
                if len(chunk) < frame_bytes:
                    continue
                frame = chunk[:frame_bytes]

                try:
                    is_vad = vad_local.is_speech(frame, _LAP_SAMPLE_RATE)
                except Exception:
                    is_vad = False

                arr   = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
                level = float(np.sqrt(np.mean(arr * arr))) / 32767.0 * 100.0

                if not speech_started:
                    preroll.append(frame)
                    waited_ms += FRAME_MS
                    # Fase D: a los 3 s sin hablar, Bob "te invita" con ojos curiosos
                    if not invite_sent and waited_ms > 3000:
                        invite_sent = True
                        pulse_emotion(self._serial, self._sm, 'CURIOSO', 700)
                    if is_vad and level > 1.5:
                        speech_started = True
                        recorded.extend(preroll)
                        speech_ms = FRAME_MS * len(preroll)
                    elif waited_ms > initial_timeout_ms:
                        return None
                else:
                    recorded.append(frame)
                    speech_ms += FRAME_MS

                    if is_vad and level > 1.5:
                        silence_streak = 0
                    else:
                        silence_streak += 1

                    if silence_streak >= _SILENCE_FRAMES:
                        break
                    if speech_ms >= MAX_RECORDING_S * 1000:
                        break

        if speech_ms < MIN_SPEECH_S * 1000:
            return None

        raw = b''.join(recorded)
        buf = _io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_LAP_SAMPLE_RATE)
            wf.writeframes(raw)
        return buf.getvalue()

    # ── STT ──────────────────────────────────────────────────────────────────

    def _transcribir(self, wav_bytes: bytes) -> str:
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._client.audio.transcriptions.create(
                    file=('audio.wav', wav_bytes, 'audio/wav'),
                    model=GROQ_STT_MODEL,
                    language='es',
                    response_format='json',
                )
                return resp.text.strip()
            except Exception as e:
                if attempt == MAX_RETRIES:
                    console.print(f'[red][voice] STT error: {e}[/]')
                    return ''
                time.sleep(0.5)
        return ''

    # ── LLM ──────────────────────────────────────────────────────────────────

    def _stream_llm(self, texto: str) -> Iterator[str]:
        self._convo.append({'role': 'user', 'content': texto})
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT}] + self._convo

        try:
            stream = self._client.chat.completions.create(
                model=GROQ_LLM_MODEL,
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                stream=True,
            )
        except Exception as e:
            console.print(f'[red][voice] LLM error: {e}[/]')
            return

        buf   = ''
        full  = ''
        for chunk in stream:
            delta = (chunk.choices[0].delta.content or '') if chunk.choices else ''
            buf  += delta
            full += delta
            while True:
                sent, buf = _split_sentence(buf)
                if sent is None:
                    break
                yield sent
        if buf.strip():
            yield buf.strip()
        self._convo.append({'role': 'assistant', 'content': full})

    # ── TTS + Reproducción ────────────────────────────────────────────────────

    def _hablar(self, texto: str, emo: Optional[str] = None) -> None:
        mp3 = _synthesize_mp3(texto, emo)
        if not mp3:
            return
        self._reproducir_mp3(mp3)

    def _reproducir_mp3(self, mp3: bytes) -> Optional[bytes]:
        """Reproduce MP3 — vía speaker del ESP32 (TCP) o pygame local según flag."""
        if USE_ROBOT_SPEAKER and self._audio_io is not None and self._audio_io.connected:
            return self._reproducir_mp3_robot(mp3)
        return self._reproducir_mp3_laptop(mp3)

    def _reproducir_mp3_robot(self, mp3: bytes) -> Optional[bytes]:
        """Convierte mp3 → uint8 @ 8 kHz y lo manda al ESP32 en chunks."""
        u8 = self._mp3_a_u8(mp3)
        if not u8:
            return None
        # Mandar header + body en bloques. send_audio_chunk mete header+body en uno.
        # Para frases grandes troceamos con header de longitud total + body por send_audio_body.
        total = len(u8)
        if not self._audio_io.send_audio_header(total):
            return None
        i = 0
        while i < total and not self._detener.is_set():
            end = min(i + TTS_SEND_CHUNK_BYTES, total)
            if not self._audio_io.send_audio_body(u8[i:end]):
                break
            i = end
            # Pacing: dejar al ESP32 reproducir
            time.sleep(TTS_SEND_CHUNK_BYTES / SAMPLE_RATE * 0.85)
        time.sleep(TTS_TAIL_S)
        return None

    @staticmethod
    def _mp3_a_u8(mp3: bytes) -> bytes:
        """Convierte mp3 → PCM uint8 mono @ SAMPLE_RATE Hz vía ffmpeg + filtros TTS."""
        proc = subprocess.run(
            [FFMPEG, '-hide_banner', '-loglevel', 'error',
             '-i', 'pipe:0',
             '-af', TTS_FFMPEG_FILTERS,
             '-ar', str(SAMPLE_RATE), '-ac', '1',
             '-acodec', 'pcm_u8', '-f', 'u8', 'pipe:1'],
            input=mp3, capture_output=True, check=False,
        )
        return proc.stdout

    def _reproducir_mp3_laptop(self, mp3: bytes) -> Optional[bytes]:
        """Reproduce MP3 con pygame en parlantes de la laptop."""
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tf:
            tf.write(mp3)
            path = tf.name
        try:
            self._pygame.mixer.music.load(path)
            self._pygame.mixer.music.play()
            while self._pygame.mixer.music.get_busy():
                time.sleep(0.05)
                if self._detener.is_set():
                    self._pygame.mixer.music.stop()
                    break
        finally:
            try:
                os.unlink(path)
            except Exception:
                pass
        return None

    def _stream_and_speak(self, texto: str) -> Optional[bytes]:
        """LLM streaming + TTS en paralelo, reproducción en cuanto llega cada frase."""
        sent_q:  queue.Queue = queue.Queue()
        audio_q: queue.Queue = queue.Queue(maxsize=8)
        captured = [None]

        def llm_worker():
            for sent in self._stream_llm(texto):
                sent_q.put(sent)
            sent_q.put(None)

        def tts_worker():
            while True:
                try:
                    sent = sent_q.get(timeout=10.0)
                except queue.Empty:
                    audio_q.put(None)
                    return
                if sent is None:
                    audio_q.put(None)
                    return
                # Extraer el tag [EMO:X] ANTES del TTS para que no se lea
                emo, clean = _extract_emo_tag(sent)
                # Saltar fragmentos sin contenido hablable (solo tag, puntuación,
                # espacios) — edge-tts lanza NoAudioReceived con esos.
                if not any(ch.isalnum() for ch in clean):
                    continue
                try:
                    mp3 = _synthesize_mp3(clean, emo)
                except Exception as e:
                    # Una frase mala no debe matar el hilo TTS entero
                    console.print(f'[red][voice] TTS error ("{clean[:40]}"): {e}[/]')
                    continue
                audio_q.put((clean, mp3, emo))

        self._sm.iniciar_hablando()

        # Fase C: si la conversación viene muy bien (mood alto o racha de 3+
        # turnos positivos), Bob arranca a hablar con cara eufórica. La emoción
        # del primer tag del LLM la reemplaza cuando empiece la primera frase.
        if self._sm.mood >= 0.6 or self._sm.positive_streak >= 3:
            self._serial.cmd_estado('MUY_FELIZ')

        t_pensando = time.monotonic()  # para detectar LLM lento (Fase D)
        primera_frase = True

        llm_t = threading.Thread(target=llm_worker, daemon=True)
        tts_t = threading.Thread(target=tts_worker, daemon=True)
        llm_t.start()
        tts_t.start()

        while True:
            try:
                item = audio_q.get(timeout=15.0)
            except queue.Empty:
                break
            if item is None:
                break
            sent_text, mp3, emo = item
            if not mp3:
                continue

            # Fase D: si el LLM tardó >2.5s, flash de "¡ya sé!" antes de hablar
            if primera_frase:
                primera_frase = False
                if time.monotonic() - t_pensando > 2.5:
                    self._serial.cmd_estado('MUY_FELIZ')
                    time.sleep(0.20)

            # Fase D: risa explícita en la frase sin tag → MUY_FELIZ
            low = sent_text.lower()
            if emo is None and ('jaja' in low or 'jeje' in low):
                emo = 'MUY_FELIZ'

            if emo:
                # El LLM decidió la emoción de esta frase — fuente principal
                self._serial.cmd_estado(emo)
                console.print(f'[bold green]Bob[/] [dim]({emo})[/]: {sent_text}')
            else:
                # Fallback si el LLM olvidó el tag: keywords como antes
                oled = 'FELIZ' if _is_happy(sent_text) else 'HABLANDO'
                self._serial.cmd_estado(oled)
                console.print(f'[bold green]Bob:[/] {sent_text}')
                react_to_bob_text(self._serial, self._sm, sent_text)

            c = self._reproducir_mp3(mp3)

            # Fase D: si la frase fue una pregunta, cara CURIOSA al terminarla
            # (la siguiente frase o la transición a LISTENING la reemplaza)
            if sent_text.rstrip().endswith('?'):
                self._serial.cmd_estado('CURIOSO')

            if c is not None:
                captured[0] = c
                break

        llm_t.join(timeout=2.0)
        tts_t.join(timeout=2.0)
        return captured[0]

    # ── Warmup ────────────────────────────────────────────────────────────────

    def _warmup(self) -> None:
        def _w():
            try:
                list(self._client.models.list(timeout=3.0))
            except Exception:
                pass
        threading.Thread(target=_w, daemon=True).start()
        threading.Thread(
            target=lambda: subprocess.run([FFMPEG, '-version'], capture_output=True, timeout=2),
            daemon=True,
        ).start()

    # ── Wake word monitor (siempre activo, corre en hilo separado) ────────────

    def iniciar_wake_monitor(self) -> None:
        """
        Hilo que graba continuamente en modo IDLE/PRESENCE solo para detectar wake word.
        Cuando detecta "Bob", notifica StateMachine.notificar_wake_word().
        Se mantiene activo mientras el robot no esté en conversación.
        """
        threading.Thread(target=self._wake_monitor_loop, daemon=True, name='wake-monitor').start()

    # ── Soliloquio / actitud (featuresAction.md Fase A) ───────────────────────

    def iniciar_soliloquio_monitor(self) -> None:
        """Hilo que, cuando Bob está libre (IDLE/PRESENCE, fuera de conversación),
        suelta de vez en cuando una frase espontánea del banco local."""
        if not SOLILOQUIO_ENABLED:
            return
        threading.Thread(target=self._soliloquio_loop, daemon=True,
                         name='soliloquio').start()

    def _soliloquio_loop(self) -> None:
        from state_machine import RobotState
        t_ultimo = 0.0
        cooldown = SOLILOQUIO_COOLDOWN_S
        carnadas = 0                      # frases-carnada disparadas en esta presencia
        while not self._detener.is_set():
            time.sleep(1.0)
            if self._detener.is_set():
                break

            est = self._sm.estado
            # Al dejar PRESENCE (se fue la persona o arrancó charla), resetear el
            # contador de carnadas para la próxima persona.
            if est != RobotState.PRESENCE:
                carnadas = 0

            # No hablar solo si hay una charla en curso o si ya estamos hablando.
            if self._sm.en_conversacion or self._muted.is_set():
                continue
            # Dormido = callado (igual que las muecas): coherencia con la pose de sueño.
            if self._sm.is_asleep():
                continue
            ahora = time.monotonic()
            if ahora - t_ultimo < cooldown:
                continue

            t_en = self._sm.t_en_estado
            if est == RobotState.IDLE and t_en >= SOLO_IDLE_MIN_S:
                categoria = random.choice(('aburrimiento', 'curiosidad', 'actitud'))
            elif est == RobotState.PRESENCE and t_en >= PRESENCE_NUDGE_S:
                # Carnada acotada: no acosar a quien no se anima a hablar.
                if carnadas >= SOLILOQUIO_MAX_CARNADAS:
                    continue
                categoria = 'carnada'
            else:
                continue

            if random.random() > P_SOLILOQUIO:
                continue
            # Última verificación: pudo entrar una conversación en este segundo.
            if self._sm.en_conversacion:
                continue

            frase = self._elegir_frase_soliloquio(categoria, est)
            if not frase:
                continue
            self.decir_soliloquio(frase)
            if categoria == 'carnada':
                carnadas += 1
            t_ultimo = time.monotonic()
            # Cadencia con jitter: que no suene metronómico.
            cooldown = SOLILOQUIO_COOLDOWN_S * random.uniform(0.8, 1.4)

    def _elegir_frase_soliloquio(self, categoria: str, estado) -> Optional[str]:
        """Mezcla LLM/banco: con prob LLM_RATIO pide una frase nueva al LLM;
        si falla o no toca, devuelve una del banco local."""
        if SOLILOQUIO_USA_LLM and random.random() < SOLILOQUIO_LLM_RATIO:
            frase = self._soliloquio_llm(categoria, estado)
            if frase:
                return frase
        frases = BANCO_SOLILOQUIO.get(categoria)
        if not frases:
            return None
        # Anti-repetición: evitar las frases dichas hace poco.
        candidatas = [f for f in frases
                      if _extract_emo_tag(f)[1].lower() not in self._soliloquio_reciente]
        return random.choice(candidatas or frases)

    def _soliloquio_llm(self, categoria: str, estado) -> Optional[str]:
        """Genera UNA frase de soliloquio con Groq. Devuelve None si falla."""
        from state_machine import RobotState
        hora = datetime.now().strftime('%H:%M')
        if estado == RobotState.PRESENCE:
            situacion = ("Hay alguien cerca mirándote pero aún no te habla; "
                         "tírale una frase para romper el hielo.")
        else:
            situacion = "No hay nadie a la vista; esperas que llegue gente."
        contexto = (f"Categoría: {categoria}. Son las {hora}. {situacion} "
                    f"Tu ánimo (de -1 a 1) está en {self._sm.mood:+.1f}.")
        if self._soliloquio_reciente:
            contexto += (" No repitas ni parafrasees estas frases que ya dijiste: "
                         + " | ".join(self._soliloquio_reciente))
        try:
            resp = self._client.chat.completions.create(
                model=GROQ_LLM_MODEL,
                messages=[{'role': 'system', 'content': SOLILOQUIO_LLM_PROMPT},
                          {'role': 'user',   'content': contexto}],
                temperature=1.0,
                max_tokens=SOLILOQUIO_LLM_MAX_TOK,
            )
            txt = (resp.choices[0].message.content or '').strip()
            if txt:
                console.print(f'[dim][soliloquio-LLM] generada: {txt}[/]')
            return txt or None
        except Exception as e:
            console.print(f'[dim][soliloquio] LLM falló, uso banco: {e}[/]')
            return None

    def decir_soliloquio(self, frase: str) -> None:
        """Dice UNA frase espontánea: muta el mic (anti-eco), pone el OLED según
        su tag [EMO], habla, restaura el OLED y deja decaer el eco."""
        emo, clean = _extract_emo_tag(frase)
        if not any(ch.isalnum() for ch in clean):
            return
        self._soliloquio_reciente.append(clean.lower())   # anti-repetición
        self._muted.set()
        self._sm.oled_ocupar()          # bloquea muecas mientras Bob habla solo
        try:
            if emo:
                self._serial.cmd_estado(emo)
            console.print(f'[bold blue][soliloquio][/] [dim]({emo})[/]: {clean}')
            self._hablar(clean, emo)
        except Exception as e:
            console.print(f'[red][soliloquio] error: {e}[/]')
        finally:
            self._sm.oled_liberar()
            self._restaurar_oled()
            time.sleep(SOLILOQUIO_SETTLE_MS / 1000.0)
            self._muted.clear()

    def _restaurar_oled(self) -> None:
        """Devuelve el OLED al look del estado actual tras un soliloquio."""
        from state_machine import RobotState, _OLED_STATE
        est = self._sm.estado
        # En PRESENCE el FacialTracker manda SIGUIENDO; no lo pisamos.
        if est == RobotState.PRESENCE:
            return
        cmd = _OLED_STATE.get(est)
        if cmd:
            self._serial.cmd_estado(cmd)

    def _wake_monitor_loop(self) -> None:
        from state_machine import RobotState
        while not self._detener.is_set():
            # Solo monitorear en estados no-conversacionales
            if self._sm.en_conversacion:
                time.sleep(0.2)
                continue

            # Anti-eco: si Bob está hablando solo, no grabar (su voz no debe
            # auto-dispararse como wake word).
            if self._muted.is_set():
                time.sleep(0.2)
                continue

            audio = self._grabar_wake()
            if audio is None:
                continue

            texto = self._transcribir(audio)
            if not texto:
                continue
            if _es_alucinacion(texto):
                continue   # silencio/ruido alucinado por Whisper → ignorar

            ww = self._wake.detect(texto)
            if ww.detected:
                payload = (ww.payload or '').strip()
                if payload:
                    console.print(f'[bold yellow][wake] "{texto}" → payload: "{payload}"[/]')
                else:
                    console.print(f'[bold yellow][wake] "{texto}" detectado![/]')
                # Pulso emocional inmediato: ojos CURIOSOS al oír su nombre
                pulse_emotion(self._serial, self._sm, EMO_WAKE_DETECTED, PULSE_FAST)
                self._sm.notificar_wake_word(payload=payload or None)
            else:
                console.print(f'[dim][wake?] heard: "{texto}"[/]')

    def _grabar_wake(self) -> Optional[bytes]:
        """Versión corta para wake monitor: ~3 s, despacha a robot o laptop."""
        if USE_ROBOT_MIC and self._audio_io is not None and self._audio_io.connected:
            return self._grabar_wake_robot()
        return self._grabar_wake_laptop()

    def _grabar_wake_robot(self) -> Optional[bytes]:
        """Graba 3 s desde el mic del ESP32 (uint8) y devuelve WAV."""
        self._audio_io.drain_mic()
        collected = bytearray()
        t0 = time.monotonic()
        while time.monotonic() - t0 < 3.0 and not self._detener.is_set():
            if self._sm.en_conversacion:
                return None
            chunk = self._audio_io.get_mic(timeout=0.1)
            if chunk:
                collected.extend(chunk)
        if not collected:
            return None
        raw = bytes(collected)
        if _rms_uint8(raw) < 4.0:        # silencio → no transcribir
            return None
        return _uint8_to_wav(raw)

    def _grabar_wake_laptop(self) -> Optional[bytes]:
        """Graba 3 s desde el mic local. Devuelve WAV int16 @ 16 kHz."""
        q: queue.Queue = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(bytes(indata))

        frame_bytes = int(_LAP_SAMPLE_RATE * FRAME_MS / 1000) * 2
        collected: List[bytes] = []
        t0 = time.monotonic()

        try:
            with sd.RawInputStream(samplerate=_LAP_SAMPLE_RATE, channels=1,
                                   dtype='int16', blocksize=frame_bytes // 2,
                                   callback=callback):
                while time.monotonic() - t0 < 3.0 and not self._detener.is_set():
                    if self._sm.en_conversacion:
                        return None
                    try:
                        chunk = q.get(timeout=0.1)
                        if len(chunk) >= frame_bytes:
                            collected.append(chunk[:frame_bytes])
                    except queue.Empty:
                        pass
        except Exception:
            return None

        if not collected:
            return None

        raw = b''.join(collected)
        # Gate de energía: si la ventana es silencio, NO transcribir (Whisper
        # alucina "Gracias." con silencio y quema llamadas a Groq).
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if arr.size:
            level = float(np.sqrt(np.mean(arr * arr))) / 32767.0 * 100.0
            if level < WAKE_MIN_LEVEL:
                return None

        buf = _io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_LAP_SAMPLE_RATE)
            wf.writeframes(raw)
        return buf.getvalue()
