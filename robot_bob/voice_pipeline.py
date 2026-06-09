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
import re
import sys
import asyncio
import queue
import subprocess
import tempfile
import threading
import time
import wave
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

# Asegurar que voicechatLap esté en el path para importar config y wake_word
_VOICECHAT_DIR = os.path.join(os.path.dirname(__file__), '..', 'voicechatLap')
if _VOICECHAT_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_VOICECHAT_DIR))

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
)
from wake_word import WakeWordDetector

FFMPEG  = imageio_ffmpeg.get_ffmpeg_exe()
console = Console()

# ── Constantes locales ─────────────────────────────────────────────────────────
_LAP_SAMPLE_RATE = 16000  # sounddevice graba a 16 kHz
_SILENCE_FRAMES  = SILENCE_END_MS // FRAME_MS
_MAX_FRAMES      = (MAX_RECORDING_S * 1000) // FRAME_MS

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

def _is_exit(text: str) -> bool:
    return text.lower().strip(' .,!?¿¡') in EXIT_PHRASES

def _is_goodbye(text: str) -> bool:
    return text.lower().strip(' .,!?¿¡') in GOODBYE_PHRASES


# ── TTS ────────────────────────────────────────────────────────────────────────

async def _edge_tts_bytes(text: str) -> bytes:
    chunks = []
    async for c in edge_tts.Communicate(text, voice=VOICE).stream():
        if c['type'] == 'audio':
            chunks.append(c['data'])
    return b''.join(chunks)

def _synthesize_mp3(text: str) -> bytes:
    if not text.strip():
        return b''
    return asyncio.run(_edge_tts_bytes(text))

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

    def __init__(self, serial_mgr, state_machine):
        self._serial = serial_mgr
        self._sm     = state_machine

        self._client = Groq(api_key=GROQ_API_KEY)
        self._vad    = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self._convo: List[Dict[str, str]] = []
        self._detener = threading.Event()

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

            self._run_one_turn(pending_audio=self._sm.pending_audio)
            self._sm.pending_audio = None

    def _run_one_turn(self, pending_audio: Optional[bytes] = None) -> None:
        # ── 1. Grabar ──────────────────────────────────────────────────────────
        if pending_audio:
            audio = pending_audio
            console.print('[dim][voice] Usando audio de barge-in[/]')
        else:
            console.print('[voice] Escuchando...')
            audio = self._grabar()

        if audio is None or not audio:
            self._sm.fin_turno()
            return

        # ── 2. Transcribir ────────────────────────────────────────────────────
        self._sm.iniciar_pensando()
        console.print('[voice] Transcribiendo...')
        texto = self._transcribir(audio)
        if not texto:
            console.print('[dim][voice] Audio sin texto[/]')
            self._sm.fin_turno()
            return

        console.print(f'[bold cyan]Usuario:[/] {texto}')

        # ── 3. Detectar wake word / comandos especiales ───────────────────────
        ww = self._wake.detect(texto)
        if WAKE_WORD_ENABLED and not self._sm.en_conversacion:
            if not ww.detected:
                # El usuario no dijo "Bob" y el robot no estaba en conversación
                self._sm.fin_turno()
                return
            texto = ww.payload or texto

        if _is_exit(texto):
            self._hablar('Hasta luego.')
            self._sm.fin_turno()
            return
        if _is_goodbye(texto):
            self._hablar('Hasta pronto.')
            self._sm.fin_turno()
            return

        # ── 4. LLM + TTS paralelo ─────────────────────────────────────────────
        console.print('[voice] Pensando...')
        captured = self._stream_and_speak(texto)

        # ── 5. Fin de turno ───────────────────────────────────────────────────
        self._sm.fin_turno(pending=captured)

    # ── Grabación ────────────────────────────────────────────────────────────

    def _grabar(self) -> Optional[bytes]:
        """Graba desde micrófono de laptop con VAD + gate adaptativo."""
        q: queue.Queue = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(bytes(indata))

        frame_bytes = int(_LAP_SAMPLE_RATE * FRAME_MS / 1000) * 2  # int16
        vad_local   = webrtcvad.Vad(VAD_AGGRESSIVENESS)

        preroll:  deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        recorded: List[bytes]  = []
        speech_started = False
        silence_streak = 0
        speech_ms      = 0
        waited_ms      = 0

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
                    if not speech_started and waited_ms > 8000:
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
                    if is_vad and level > 1.5:
                        speech_started = True
                        recorded.extend(preroll)
                        speech_ms = FRAME_MS * len(preroll)
                    elif waited_ms > 8000:
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

    def _hablar(self, texto: str) -> None:
        mp3 = _synthesize_mp3(texto)
        if not mp3:
            return
        self._reproducir_mp3(mp3)

    def _reproducir_mp3(self, mp3: bytes) -> Optional[bytes]:
        """Reproduce MP3 con pygame. Devuelve None (barge-in no soportado en laptop sin ESP32)."""
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
                mp3 = _synthesize_mp3(sent)
                audio_q.put((sent, mp3))

        self._sm.iniciar_hablando()
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
            sent_text, mp3 = item
            if not mp3:
                continue

            oled = 'FELIZ' if _is_happy(sent_text) else 'HABLANDO'
            self._serial.cmd_estado(oled)
            console.print(f'[bold green]Bob:[/] {sent_text}')

            c = self._reproducir_mp3(mp3)
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

    def _wake_monitor_loop(self) -> None:
        from state_machine import RobotState
        while not self._detener.is_set():
            # Solo monitorear en estados no-conversacionales
            if self._sm.en_conversacion:
                time.sleep(0.2)
                continue

            audio = self._grabar_wake()
            if audio is None:
                continue

            texto = self._transcribir(audio)
            if not texto:
                continue

            ww = self._wake.detect(texto)
            if ww.detected:
                console.print(f'[bold yellow][wake] "{texto}" detectado![/]')
                self._sm.notificar_wake_word()

    def _grabar_wake(self) -> Optional[bytes]:
        """Versión corta de _grabar: graba máximo 3 s, sin esperar largo silencio."""
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
        buf = _io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_LAP_SAMPLE_RATE)
            wf.writeframes(raw)
        return buf.getvalue()
