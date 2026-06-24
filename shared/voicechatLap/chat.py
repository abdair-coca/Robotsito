"""
chat.py — Super chat conversacional para Creeper sobre el ESP32.

Adapta la lógica de localConv.py (VAD + STT + LLM + TTS + wake word + barge-in)
al transporte del ESP32: audio uint8 8 kHz mono. El mic viene en streaming
continuo desde AudioIO; el playback se manda en chunks y el barge-in puede
disparar un STOP que el firmware atiende inmediatamente.
"""

from __future__ import annotations

import io as _io
import os
import re
import time
import wave
import queue
import asyncio
import tempfile
import threading
import traceback
import subprocess
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
from rich.panel import Panel

from audio_io import AudioIO
from robot_serial import RobotSerial, is_happy
from config import (
    GROQ_API_KEY,
    USE_ROBOT_SPEAKER,
    USE_ROBOT_MIC,
    SAMPLE_RATE, FRAME_MS, FRAME_SIZE, MIC_CHUNK_BYTES,
    VAD_AGGRESSIVENESS, SILENCE_END_MS, MAX_RECORDING_S, MIN_SPEECH_S, PREROLL_FRAMES,
    NOISE_FLOOR_INIT, NOISE_FLOOR_MARGIN, NOISE_FLOOR_MIN, SPEECH_START_FRAMES,
    BARGE_IN_ENABLED, BARGE_IN_SUSTAINED_MS, BARGE_IN_SETTLE_MS, BARGE_IN_RMS_U8,
    WAKE_WORD_ENABLED, WAKE_WORDS, CONVERSATION_TIMEOUT_S,
    WAKE_CANONICAL, WAKE_PREFIXES, WAKE_MIN_CONF, WAKE_FUZZY_THR,
    WAKE_COOLDOWN_S, WAKE_MAX_UTTR_CHARS,
    GROQ_LLM_MODEL, GROQ_STT_MODEL, TEMPERATURE, MAX_TOKENS, MAX_RETRIES,
    VOICE, TTS_FFMPEG_FILTERS, TTS_SEND_CHUNK_BYTES, SENTENCE_MIN_CHARS, TTS_TAIL_S,
    SERIAL_PORT, SERIAL_BAUD,
    SYSTEM_PROMPT, EXIT_PHRASES, GOODBYE_PHRASES,
)
from wake_word import WakeWordDetector


FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
console = Console()


# =========================
# Conversiones de audio
# =========================

def uint8_to_int16_bytes(uint8_buf: bytes) -> bytes:
    """uint8 -> int16 PCM little-endian centrado en 0, removiendo el offset DC de forma dinámica."""
    arr = np.frombuffer(uint8_buf, dtype=np.uint8).astype(np.float32)
    if arr.size == 0:
        return b""
    arr = (arr - arr.mean()) * 256.0
    return np.clip(arr, -32768, 32767).astype(np.int16).tobytes()


def rms_uint8(uint8_buf: bytes) -> float:
    """RMS de un frame uint8 centrado dinámicamente para eliminar el DC offset y medir la energía real."""
    arr = np.frombuffer(uint8_buf, dtype=np.uint8).astype(np.float32)
    if arr.size == 0:
        return 0.0
    arr = arr - arr.mean()
    return float(np.sqrt(np.mean(arr * arr)))


# Filtro pasa-banda 80–3500 Hz precalculado para el preprocesamiento del mic.
# Lo dejamos como módulo-level para no recalcularlo en cada turno.
_VOICE_BANDPASS = butter(4, [80, 3500], btype="band", fs=SAMPLE_RATE, output="sos")


def preprocess_mic_for_stt(uint8_audio: bytes) -> np.ndarray:
    """
    Limpia el audio crudo del ESP32 para que Whisper se equivoque menos:
      1) uint8 -> float32 centrado en 0
      2) resta DC residual
      3) bandpass 80–3500 Hz (rango de voz humana)
      4) normaliza al pico para usar todo el rango int16
    Devuelve np.int16 listo para empaquetar en WAV.
    """
    audio = np.frombuffer(uint8_audio, dtype=np.uint8).astype(np.float32) - 128.0
    # eliminar offset DC residual (capacitor de la entrada del ESP32 no es perfecto)
    audio -= audio.mean()
    # filtro de voz
    audio = sosfilt(_VOICE_BANDPASS, audio).astype(np.float32)
    # normalizar pico al 90% del int16
    peak = np.max(np.abs(audio))
    if peak > 1.0:
        audio = audio / peak * (0.9 * 32767.0)
    return audio.astype(np.int16)


def uint8_frames_to_wav_bytes(uint8_audio: bytes) -> bytes:
    """Empaqueta uint8 del ESP32 como WAV int16 limpio, listo para Groq."""
    int16_arr = preprocess_mic_for_stt(uint8_audio)
    buf = _io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(int16_arr.tobytes())
    return buf.getvalue()


# =========================
# Lector de frames del ESP32
# =========================

class FrameReader:
    """Convierte la cola de chunks variables de AudioIO en frames de tamaño
    fijo (FRAME_SIZE bytes uint8 = 30 ms) para alimentar al VAD."""

    def __init__(self, io: AudioIO):
        self.io = io
        self.buf = bytearray()

    def next_frame(self, timeout: float = 1.0) -> Optional[bytes]:
        deadline = time.monotonic() + timeout
        while len(self.buf) < FRAME_SIZE:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            chunk = self.io.get_mic(timeout=remaining)
            if chunk is None:
                continue
            self.buf.extend(chunk)
        frame = bytes(self.buf[:FRAME_SIZE])
        del self.buf[:FRAME_SIZE]
        return frame

    def drain(self) -> None:
        self.buf.clear()
        self.io.drain_mic()


# =========================
# TTS: edge-tts + ffmpeg -> uint8 raw
# =========================

async def _edge_tts_to_bytes(text: str) -> bytes:
    """edge-tts -> bytes MP3 (en memoria, sin archivo temp)."""
    chunks = []
    async for chunk in edge_tts.Communicate(text, voice=VOICE).stream():
        if chunk["type"] == "audio":
            chunks.append(chunk["data"])
    return b"".join(chunks)


def synthesize_to_u8(text: str) -> bytes:
    """text -> bytes uint8 8 kHz mono crudo, listo para el DAC del ESP32.
    Hace todo en memoria: edge-tts -> MP3 bytes -> ffmpeg (stdin/stdout) -> uint8."""
    if not text or not text.strip():
        return b""

    mp3_bytes = asyncio.run(_edge_tts_to_bytes(text))
    if not mp3_bytes:
        return b""

    # ffmpeg en modo pipe: MP3 por stdin, uint8 raw (sin header WAV) por stdout.
    # "-f u8" indica formato de salida sin contenedor.
    proc = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-af", TTS_FFMPEG_FILTERS,
            "-ar", str(SAMPLE_RATE),
            "-ac", "1",
            "-acodec", "pcm_u8",
            "-f", "u8",
            "pipe:1",
        ],
        input=mp3_bytes,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        # log y devolvemos vacío en lugar de tirar excepción que mate el thread
        try:
            console.print(f"[red]ffmpeg error: {proc.stderr.decode('utf-8', 'ignore')[:200]}[/]")
        except Exception:
            pass
        return b""
    return proc.stdout


# =========================
# Wake word helpers — DEPRECATED
# =========================
# Se mantienen como wrappers para no romper código que los importe directamente,
# pero internamente usan el nuevo WakeWordDetector. Usá `self.wake_detector`
# en código nuevo.

_WAKE_LEGACY_DETECTOR = WakeWordDetector(
    wake_word=WAKE_CANONICAL,
    prefixes=WAKE_PREFIXES,
    min_confidence=WAKE_MIN_CONF,
    fuzzy_threshold=WAKE_FUZZY_THR,
    cooldown_s=0.0,                 # legacy helpers no manejan cooldown
    max_utterance_chars=WAKE_MAX_UTTR_CHARS,
)

def contains_wake_word(text: str) -> bool:
    return _WAKE_LEGACY_DETECTOR.detect(text).detected


def strip_wake_word(text: str) -> str:
    r = _WAKE_LEGACY_DETECTOR.detect(text)
    return r.payload if r.detected else text


def is_exit(text: str) -> bool:
    cleaned = text.lower().strip(" .,!?¿¡")
    return cleaned in EXIT_PHRASES


def is_goodbye(text: str) -> bool:
    cleaned = text.lower().strip(" .,!?¿¡")
    return cleaned in GOODBYE_PHRASES


# =========================
# Sentence splitter (para streaming TTS)
# =========================

_SENT_END_RE = re.compile(r"[.!?¡¿…\n]+[\s\"')\]]*")


def split_sentences_stream(buf: str, min_chars: int = SENTENCE_MIN_CHARS) -> tuple[Optional[str], str]:
    """
    Recibe un buffer de tokens del LLM y devuelve (sentence, remaining).
    sentence != None si encontró un terminador y tiene al menos min_chars.
    Si no, espera más tokens.
    """
    if len(buf) < min_chars:
        return None, buf
    m = _SENT_END_RE.search(buf, min_chars - 1)
    if m is None:
        return None, buf
    end = m.end()
    return buf[:end].strip(), buf[end:]


# =========================
# Chat
# =========================

class Chat:
    def __init__(self, io: AudioIO):
        self.io = io
        self.reader = FrameReader(io)
        self.client = Groq(api_key=GROQ_API_KEY)
        self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self.conversation: List[Dict[str, str]] = []
        self.awake = not WAKE_WORD_ENABLED
        self.last_interaction = time.time()
        self.last_user_text: str = ""
        # Detector con cooldown stateful — propio de esta sesión
        self.wake_detector = WakeWordDetector(
            wake_word=WAKE_CANONICAL,
            prefixes=WAKE_PREFIXES,
            min_confidence=WAKE_MIN_CONF,
            fuzzy_threshold=WAKE_FUZZY_THR,
            cooldown_s=WAKE_COOLDOWN_S,
            max_utterance_chars=WAKE_MAX_UTTR_CHARS,
        )
        # Serial al ESP32 para los comandos ESTADO (OLED). Si el puerto está
        # ocupado o no existe, queda en no-op silencioso.
        self.serial = RobotSerial(SERIAL_PORT, SERIAL_BAUD) if SERIAL_PORT else None
        if self.serial and self.serial.connected:
            console.print(f"[dim]Serial al ESP32 abierto en {SERIAL_PORT}[/]")
        else:
            console.print(f"[dim]Serial al ESP32 no disponible (OK, sigue sin OLED)[/]")
        
        # Inicializar Pygame mixer si usamos altavoz local
        if not USE_ROBOT_SPEAKER:
            import pygame
            try:
                if not pygame.mixer.get_init():
                    pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)
                console.print("[dim]Pygame mixer inicializado para reproducción local.[/]")
            except Exception as e:
                console.print(f"[red]✗ Error al inicializar pygame mixer: {e}[/]")
                
        self._warmup()

    def _estado(self, estado: str) -> None:
        """Atajo: manda ESTADO:XX al ESP32 si el serial está activo."""
        if self.serial is not None:
            self.serial.estado(estado)

    def close(self) -> None:
        """Libera recursos. Idempotente."""
        if self.serial is not None:
            try:
                self.serial.estado("ESPERANDO")
            except Exception:
                pass
            self.serial.close()
            self.serial = None

    def _warmup(self) -> None:
        """Precalienta lo que tarda en la primera llamada:
          - Conexión HTTPS con Groq (TLS handshake ~300-400 ms)
          - Binario de ffmpeg cargado en OS cache (~50-100 ms en la 1ra invocación)
        Se ejecuta en threads para no bloquear el arranque.
        """
        def warm_groq():
            try:
                # call cheap que abre la conexión TLS y la deja en el pool de httpx
                list(self.client.models.list(timeout=3.0))
            except Exception:
                pass

        def warm_ffmpeg():
            try:
                subprocess.run(
                    [FFMPEG, "-version"],
                    capture_output=True, timeout=2,
                )
            except Exception:
                pass

        threading.Thread(target=warm_groq, daemon=True).start()
        threading.Thread(target=warm_ffmpeg, daemon=True).start()

    # ----- record (VAD endpointing) -----
 
    def record_utterance(self, initial_timeout_s: Optional[float]) -> Optional[bytes]:
        """
        Graba audio. Si USE_ROBOT_MIC es True, graba del robot (ESP32 uint8).
        Si es False, graba del micrófono local de la laptop (sounddevice float32).
        """
        if USE_ROBOT_MIC:
            # Grabar desde el micrófono del robot (ESP32)
            self.reader.drain()

            silence_frames_needed = SILENCE_END_MS // FRAME_MS
            max_frames = (MAX_RECORDING_S * 1000) // FRAME_MS

            preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
            recorded: List[bytes] = []
            speech_started = False
            silence_streak = 0
            waited_ms = 0
            speech_ms = 0

            # Gate adaptativo
            noise_floor = NOISE_FLOOR_INIT
            speech_run = 0           # frames consecutivos clasificados como voz real

            while True:
                frame = self.reader.next_frame(timeout=1.0)
                if frame is None:
                    if (
                        not speech_started
                        and initial_timeout_s is not None
                        and waited_ms / 1000.0 >= initial_timeout_s
                    ):
                        return None
                    if not self.io.connected:
                        return None
                    continue

                is_vad = self.vad.is_speech(uint8_to_int16_bytes(frame), SAMPLE_RATE)
                level = rms_uint8(frame)
                # Voz real = VAD positivo Y nivel claramente sobre el piso
                speech_threshold = max(noise_floor * NOISE_FLOOR_MARGIN, NOISE_FLOOR_MIN)
                is_real_speech = is_vad and level > speech_threshold

                if not speech_started:
                    preroll.append(frame)
                    # Aprender el piso de ruido solo con frames que NO son voz
                    if not is_vad:
                        noise_floor = 0.92 * noise_floor + 0.08 * level

                    if is_real_speech:
                        speech_run += 1
                        if speech_run >= SPEECH_START_FRAMES:
                            # arrancamos: el preroll ya contiene el primer audio
                            speech_started = True
                            recorded.extend(preroll)
                            speech_ms = FRAME_MS * len(preroll)
                    else:
                        speech_run = 0
                        waited_ms += FRAME_MS
                        if (
                            initial_timeout_s is not None
                            and waited_ms / 1000.0 >= initial_timeout_s
                        ):
                            return None
                else:
                    recorded.append(frame)
                    speech_ms += FRAME_MS
                    if is_real_speech:
                        silence_streak = 0
                    else:
                        silence_streak += 1
                        # mientras estemos en "silencio aparente", el piso puede seguir subiendo
                        if not is_vad:
                            noise_floor = 0.95 * noise_floor + 0.05 * level
                        if silence_streak >= silence_frames_needed:
                            break
                    if len(recorded) >= max_frames:
                        break

            if not speech_started or speech_ms / 1000.0 < MIN_SPEECH_S:
                return None
            if silence_streak > 0:
                recorded = recorded[:-silence_streak]
            return b"".join(recorded)

        else:
            # Grabar desde el micrófono local de la laptop (sounddevice float32)
            LAP_SAMPLE_RATE = 16000
            LAP_FRAME_MS = 30
            LAP_FRAME_SIZE = int(LAP_SAMPLE_RATE * LAP_FRAME_MS / 1000)  # 480 muestras

            vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
            silence_frames_needed = SILENCE_END_MS // LAP_FRAME_MS
            max_frames = (MAX_RECORDING_S * 1000) // LAP_FRAME_MS

            preroll_lap: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
            recorded_lap: List[bytes] = []
            speech_started = False
            silence_streak = 0
            waited_ms = 0
            speech_ms = 0
            q: queue.Queue = queue.Queue()

            def cb(indata, frames, time_info, status):
                q.put(indata.copy())

            try:
                with sd.InputStream(
                    samplerate=LAP_SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=LAP_FRAME_SIZE,
                    callback=cb,
                ):
                    while True:
                        try:
                            frame = q.get(timeout=1.0).flatten()
                        except queue.Empty:
                            continue

                        # Convertir float32 a PCM 16-bit
                        pcm16 = (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                        is_speech = vad.is_speech(pcm16, LAP_SAMPLE_RATE)

                        if not speech_started:
                            preroll_lap.append(pcm16)
                            if is_speech:
                                speech_started = True
                                recorded_lap.extend(preroll_lap)
                                speech_ms = LAP_FRAME_MS * len(preroll_lap)
                            else:
                                waited_ms += LAP_FRAME_MS
                                if initial_timeout_s is not None and waited_ms / 1000.0 >= initial_timeout_s:
                                    return None
                        else:
                            recorded_lap.append(pcm16)
                            speech_ms += LAP_FRAME_MS
                            if is_speech:
                                silence_streak = 0
                            else:
                                silence_streak += 1
                                if silence_streak >= silence_frames_needed:
                                    break
                            if len(recorded_lap) >= max_frames:
                                break
            except Exception as e:
                console.print(f"[red]✗ No se pudo acceder al micrófono de la laptop: {e}[/]")
                return None

            if not speech_started or speech_ms / 1000.0 < MIN_SPEECH_S:
                return None

            if silence_streak > 0:
                recorded_lap = recorded_lap[:-silence_streak]

            # Convertir a bytes WAV de 16 kHz mono
            buf = _io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(LAP_SAMPLE_RATE)
                wf.writeframes(b"".join(recorded_lap))
            return buf.getvalue()

    # ----- STT -----
 
    def transcribe(self, audio_bytes: bytes) -> str:
        if USE_ROBOT_MIC:
            wav_bytes = uint8_frames_to_wav_bytes(audio_bytes)
        else:
            wav_bytes = audio_bytes

        last_exc: Optional[Exception] = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self.client.audio.transcriptions.create(
                    file=("audio.wav", wav_bytes, "audio/wav"),
                    model=GROQ_STT_MODEL,
                    language="es",
                    response_format="json",
                )
                return (getattr(resp, "text", "") or "").strip()
            except Exception as e:
                last_exc = e
                time.sleep(0.4 * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    # ----- LLM (streaming token-por-token) -----

    def stream_llm_sentences(self, user_text: str) -> Iterator[str]:
        """
        Stream del LLM. Acumula tokens hasta tener una frase completa
        (terminador + min chars) y la emite. Al terminar, agrega el reply
        completo al historial.
        """
        self.conversation.append({"role": "user", "content": user_text})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.conversation

        try:
            stream = self.client.chat.completions.create(
                model=GROQ_LLM_MODEL,
                messages=messages,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                stream=True,
            )
        except Exception:
            self.conversation.pop()
            raise

        buf = ""
        full = ""
        try:
            for chunk in stream:
                delta = ""
                try:
                    delta = chunk.choices[0].delta.content or ""
                except (AttributeError, IndexError):
                    pass
                if not delta:
                    continue
                buf += delta
                full += delta
                while True:
                    sentence, rest = split_sentences_stream(buf)
                    if sentence is None:
                        break
                    buf = rest
                    if sentence:
                        yield sentence
            # cola: emitir lo que quedó sin terminador
            tail = (buf + "").strip()
            if tail:
                yield tail
        finally:
            full = full.strip()
            if full:
                self.conversation.append({"role": "assistant", "content": full})
            else:
                # no hubo respuesta: revertir el user
                self.conversation.pop()

    # ----- barge-in listener -----

    def _barge_in_listener(
        self,
        captured: list[Optional[bytes]],
        stop_playback_event: threading.Event,
        is_busy_cb: callable,
        stop_cb: callable
    ):
        """
        Escucha el mic de la laptop mientras el robot o la laptop habla.
        Si detecta interrupción:
          1. Activa stop_playback_event y llama a stop_cb().
          2. Sigue grabando hasta silencio.
          3. Guarda el WAV resultante en captured[0].
        """
        LAP_SAMPLE_RATE = 16000
        LAP_FRAME_MS = 30
        LAP_FRAME_SIZE = int(LAP_SAMPLE_RATE * LAP_FRAME_MS / 1000)  # 480 muestras

        vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        sustained_needed = max(1, 150 // LAP_FRAME_MS)
        silence_frames_needed = SILENCE_END_MS // LAP_FRAME_MS
        settle_frames = min(BARGE_IN_SETTLE_MS, 200) // LAP_FRAME_MS
        max_frames = (MAX_RECORDING_S * 1000) // LAP_FRAME_MS
        grace_frames_needed = 10  # ~300ms post-playback antes de salir

        sustained = 0
        frames_seen = 0
        grace_frames = 0
        speech_started = False
        silence_streak = 0
        speech_ms = 0
        preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        recorded: list[bytes] = []

        q: queue.Queue = queue.Queue()

        def cb(indata, frames, time_info, status):
            q.put(indata.copy())

        try:
            with sd.InputStream(
                samplerate=LAP_SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=LAP_FRAME_SIZE,
                callback=cb,
            ):
                while True:
                    if not speech_started and not is_busy_cb():
                        grace_frames += 1
                        if grace_frames >= grace_frames_needed:
                            return
                    if speech_started and len(recorded) >= max_frames:
                        break

                    try:
                        frame = q.get(timeout=0.1).flatten()
                    except queue.Empty:
                        continue

                    frames_seen += 1
                    # Convertir float32 a PCM 16-bit
                    pcm16 = (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16).tobytes()

                    # ignoramos el arranque del TTS para evitar eco
                    if is_busy_cb() and frames_seen < settle_frames:
                        continue

                    is_vad = vad.is_speech(pcm16, LAP_SAMPLE_RATE)
                    # Medimos el RMS del float32 para el gate de nivel
                    level = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))) if frame.size > 0 else 0.0

                    if not speech_started:
                        preroll.append(pcm16)
                        # Mientras suene la voz, exigimos VAD Y RMS;
                        # si ya terminó, basta con VAD
                        if is_busy_cb():
                            looks_like_speech = is_vad and level > 0.010
                        else:
                            looks_like_speech = is_vad

                        if looks_like_speech:
                            sustained += 1
                            if sustained >= sustained_needed:
                                speech_started = True
                                recorded.extend(preroll)
                                speech_ms = LAP_FRAME_MS * len(preroll)
                                stop_playback_event.set()
                                stop_cb()
                        else:
                            sustained = max(0, sustained - 1)
                    else:
                        recorded.append(pcm16)
                        speech_ms += LAP_FRAME_MS
                        if is_vad:
                            silence_streak = 0
                        else:
                            silence_streak += 1
                            if silence_streak >= silence_frames_needed:
                                break
        except Exception as e:
            # Si el mic no se puede abrir, simplemente salimos sin activar barge-in
            console.print(f"[dim]Barge-in: no se pudo abrir el mic de la laptop: {e}[/]")
            return

        if speech_started and speech_ms / 1000.0 >= MIN_SPEECH_S:
            if silence_streak > 0:
                recorded = recorded[:-silence_streak]
            
            buf = _io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(LAP_SAMPLE_RATE)
                wf.writeframes(b"".join(recorded))
            captured[0] = buf.getvalue()

    # ----- speak -----

    def speak(self, audio: bytes) -> Optional[bytes]:
        """
        Reproduce audio. Si USE_ROBOT_SPEAKER es True, lo transmite al robot (u8).
        Si es False, lo reproduce localmente vía pygame mixer (MP3).
        """
        if not audio:
            return None

        # Configurar eventos para control del barge-in
        stop_playback_event = threading.Event()
        captured: list[Optional[bytes]] = [None]
        watcher: Optional[threading.Thread] = None

        if USE_ROBOT_SPEAKER:
            total = len(audio)
            playback_rate = SAMPLE_RATE
            send_rate = int(SAMPLE_RATE * 1.15)
            chunk_size = TTS_SEND_CHUNK_BYTES

            # 1) Abrir el mensaje grande
            if not self.io.send_audio_header(total):
                return None

            # 2) Pre-fill
            prefill = min(4096, total)
            if not self.io.send_audio_body(audio[:prefill]):
                return None
            sent = prefill
            start = time.monotonic()

            # Definir callbacks de control para el watcher
            is_busy = lambda: (sent < total or (time.monotonic() - start) < (total / playback_rate) + TTS_TAIL_S) and not stop_playback_event.is_set()
            stop_cb = lambda: self.io.send_stop()

            # Arrancar watcher de barge-in sólo si usamos el mic de la laptop y está habilitado el barge-in
            if not USE_ROBOT_MIC and BARGE_IN_ENABLED:
                watcher = threading.Thread(
                    target=self._barge_in_listener,
                    args=(captured, stop_playback_event, is_busy, stop_cb),
                    daemon=True
                )
                watcher.start()

            # 3) Pacing de envío
            while sent < total and not stop_playback_event.is_set():
                elapsed = time.monotonic() - start
                target = int(elapsed * send_rate) + chunk_size
                if sent < target:
                    end = min(sent + chunk_size, total)
                    if not self.io.send_audio_body(audio[sent:end]):
                        break
                    sent = end
                else:
                    time.sleep(0.01)

            # 4) Esperar a que el ESP32 termine de reproducir si no fue cancelado
            if not stop_playback_event.is_set():
                expected_duration = total / playback_rate
                elapsed = time.monotonic() - start
                while elapsed < expected_duration + TTS_TAIL_S and not stop_playback_event.is_set():
                    time.sleep(0.01)
                    elapsed = time.monotonic() - start

            if watcher is not None:
                watcher.join(timeout=MAX_RECORDING_S + 1.0)

            return captured[0]
        else:
            # Reproducción local vía pygame (MP3 bytes)
            import pygame
            f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            temp_path = f.name
            try:
                f.write(audio)
                f.close()
                pygame.mixer.music.load(temp_path)
                pygame.mixer.music.play()

                is_busy = lambda: pygame.mixer.music.get_busy()
                stop_cb = lambda: pygame.mixer.music.stop()

                # Arrancar watcher de barge-in sólo si usamos el mic de la laptop y está habilitado el barge-in
                if not USE_ROBOT_MIC and BARGE_IN_ENABLED:
                    watcher = threading.Thread(
                        target=self._barge_in_listener,
                        args=(captured, stop_playback_event, is_busy, stop_cb),
                        daemon=True
                    )
                    watcher.start()

                while pygame.mixer.music.get_busy() and not stop_playback_event.is_set():
                    time.sleep(0.02)
            finally:
                try:
                    pygame.mixer.music.unload()
                except Exception:
                    pass
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

            if watcher is not None:
                watcher.join(timeout=MAX_RECORDING_S + 1.0)

            return captured[0]

    def say(self, text: str) -> Optional[bytes]:
        """Atajo: sintetiza un texto y lo reproduce."""
        try:
            if USE_ROBOT_SPEAKER:
                audio = synthesize_to_u8(text)
            else:
                audio = asyncio.run(_edge_tts_to_bytes(text))
        except Exception:
            traceback.print_exc()
            return None
        return self.speak(audio)

    # ----- Pipeline streaming: LLM -> TTS por frase -> reproducir -----

    def stream_and_speak(self, user_text: str) -> tuple[str, Optional[bytes]]:
        """
        Arranca el stream del LLM y, en paralelo, un worker que sintetiza
        cada frase emitida. Mientras una frase se reproduce, las siguientes
        se van sintetizando. Esto recorta dramáticamente la latencia hasta
        oír la primera palabra.

        Devuelve (texto_completo_emitido, captured_audio_si_barge_in_o_None).
        """
        # Cola de frases (texto) que entran al worker TTS
        sentence_q: "queue.Queue[Optional[str]]" = queue.Queue()
        # Cola de audio sintetizado lista para reproducir
        audio_q: "queue.Queue[Optional[bytes]]" = queue.Queue(maxsize=8)
        cancel = threading.Event()
        full_text = []
        captured: Optional[bytes] = None

        def tts_worker():
            while True:
                if cancel.is_set():
                    return
                try:
                    sent = sentence_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if sent is None:
                    audio_q.put(None)
                    return
                if cancel.is_set():
                    return
                try:
                    if USE_ROBOT_SPEAKER:
                        audio = synthesize_to_u8(sent)
                    else:
                        audio = asyncio.run(_edge_tts_to_bytes(sent))
                except Exception:
                    traceback.print_exc()
                    continue
                if cancel.is_set():
                    return
                # Pasamos también el texto para que el main loop pueda
                # decidir el ESTADO emocional (HABLANDO vs FELIZ).
                audio_q.put((sent, audio))

        def llm_worker():
            try:
                for sentence in self.stream_llm_sentences(user_text):
                    if cancel.is_set():
                        return
                    full_text.append(sentence)
                    console.print(f"[green]🤖[/] {sentence}")
                    sentence_q.put(sentence)
            except Exception:
                traceback.print_exc()
            finally:
                sentence_q.put(None)

        tts_thread = threading.Thread(target=tts_worker, daemon=True)
        llm_thread = threading.Thread(target=llm_worker, daemon=True)
        tts_thread.start()
        llm_thread.start()

        # Main: pulea audio en cuanto esté disponible
        try:
            while True:
                item = audio_q.get()
                if item is None:
                    break
                if cancel.is_set():
                    break
                sent_text, audio = item
                # Cara según el contenido de la frase: si suena positiva => FELIZ
                self._estado("FELIZ" if is_happy(sent_text) else "HABLANDO")
                captured = self.speak(audio)
                if captured is not None:
                    cancel.set()
                    break
        finally:
            cancel.set()
            # drenar cualquier audio pendiente
            try:
                while True:
                    audio_q.get_nowait()
            except queue.Empty:
                pass
            # despertar al worker si está esperando
            try:
                sentence_q.put_nowait(None)
            except queue.Full:
                pass
            llm_thread.join(timeout=2.0)
            tts_thread.join(timeout=2.0)
            # volver a estado neutro
            self._estado("ESPERANDO")

        return ("".join(full_text), captured)

    # ----- loop principal -----

    def run(self) -> None:
        console.print(Panel.fit(
            f"[bold green]🤖 Creeper conectado al ESP32[/]\n"
            f"Voz: [cyan]{VOICE}[/]  •  "
            f"Micrófono: [cyan]{'Robot' if USE_ROBOT_MIC else 'Laptop'}[/]  •  "
            f"Altavoz: [cyan]{'Robot' if USE_ROBOT_SPEAKER else 'Laptop'}[/]\n"
            f"Wake word: [cyan]{'ON' if WAKE_WORD_ENABLED else 'OFF'}[/]  •  "
            f"Barge-in: [cyan]{'ON' if (BARGE_IN_ENABLED and not USE_ROBOT_MIC) else 'OFF'}[/]\n"
            f"[dim]Di 'adiós' para salir. Ctrl+C también funciona.[/]",
            title="Creeper", border_style="green",
        ))

        pending_audio: Optional[bytes] = None

        while self.io.connected:
            try:
                # Re-armar wake word tras inactividad
                if (
                    WAKE_WORD_ENABLED
                    and self.awake
                    and (time.time() - self.last_interaction) > CONVERSATION_TIMEOUT_S
                ):
                    console.print("[dim]💤 Volviendo a modo wake word...[/]")
                    self.awake = False
                    self.reader.drain()
                    # liberar el cooldown para que la próxima frase pueda disparar
                    self.wake_detector.reset_cooldown()

                # 1) Obtener audio del usuario
                if pending_audio is not None:
                    audio = pending_audio
                    pending_audio = None
                elif not self.awake:
                    console.print("[dim]👂 Esperando 'Creeper'...[/]")
                    self._estado("ESPERANDO")
                    audio = self.record_utterance(initial_timeout_s=None)
                else:
                    console.print("[bold]🎤 Escuchando...[/]")
                    self._estado("ESCUCHANDO")
                    audio = self.record_utterance(initial_timeout_s=CONVERSATION_TIMEOUT_S)
                    if audio is None and WAKE_WORD_ENABLED:
                        self.awake = False
                        continue

                if audio is None:
                    continue

                # 2) Transcribir (PENSANDO: el OLED muestra cara de pensar)
                self._estado("PENSANDO")
                try:
                    with console.status("[cyan]Transcribiendo...[/]", spinner="dots"):
                        text = self.transcribe(audio)
                except Exception:
                    console.print("[red]✗ Error al transcribir.[/]")
                    self.say("No te escuché bien, ¿puedes repetir?")
                    continue

                if not text:
                    continue

                # 3) Gate wake word — usa el detector con confidence + cooldown
                if not self.awake:
                    wake_res = self.wake_detector.detect(text)
                    if not wake_res.detected:
                        console.print(
                            f"[dim]…ignorado:[/] {text!r} "
                            f"[dim](conf={wake_res.confidence:.2f}, {wake_res.reason})[/]"
                        )
                        continue
                    self.awake = True
                    self.last_interaction = time.time()
                    payload = wake_res.payload
                    console.print(
                        f"[bold cyan]🧑 Tú:[/] {text} "
                        f"[dim](wake conf={wake_res.confidence:.2f}, {wake_res.reason})[/]"
                    )
                    if not payload:
                        self.say("¿Sí?")
                        continue
                else:
                    payload = text
                    console.print(f"[bold cyan]🧑 Tú:[/] {text}")

                self.last_interaction = time.time()

                # 4) Salida / despedida / palabra repetida
                if is_exit(payload):
                    self.say("Hasta luego.")
                    return

                if is_goodbye(payload):
                    self.say("¡Hasta luego! Llámame si me necesitas.")
                    self.awake = False
                    self.reader.drain()
                    self.last_user_text = ""
                    continue

                normalized = payload.lower().strip(" .,!?¿¡")
                if normalized and normalized == self.last_user_text:
                    self.say("¡Hasta pronto!")
                    self.awake = False
                    self.reader.drain()
                    self.last_user_text = ""
                    continue
                self.last_user_text = normalized

                # 5+6) LLM streaming + TTS por frase + reproducción con barge-in
                try:
                    _full, captured = self.stream_and_speak(payload)
                except Exception:
                    console.print("[red]✗ Sin respuesta de Groq tras reintentos.[/]")
                    self.say("No pude conectarme. Intenta de nuevo en un momento.")
                    continue

                if captured is not None:
                    console.print("[yellow]↳ interrumpido[/]")
                    pending_audio = captured
                    self.awake = True
                self.last_interaction = time.time()
                self.last_user_text = ""

            except KeyboardInterrupt:
                raise
            except Exception:
                traceback.print_exc()
                time.sleep(0.3)
