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
from scipy.signal import butter, sosfilt
from groq import Groq
from rich.console import Console
from rich.panel import Panel

from audio_io import AudioIO
from robot_serial import RobotSerial, is_happy
from config import (
    GROQ_API_KEY,
    SAMPLE_RATE, FRAME_MS, FRAME_SIZE, MIC_CHUNK_BYTES,
    VAD_AGGRESSIVENESS, SILENCE_END_MS, MAX_RECORDING_S, MIN_SPEECH_S, PREROLL_FRAMES,
    NOISE_FLOOR_INIT, NOISE_FLOOR_MARGIN, NOISE_FLOOR_MIN, SPEECH_START_FRAMES,
    BARGE_IN_ENABLED, BARGE_IN_SUSTAINED_MS, BARGE_IN_SETTLE_MS, BARGE_IN_RMS_U8,
    WAKE_WORD_ENABLED, WAKE_WORDS, CONVERSATION_TIMEOUT_S,
    GROQ_LLM_MODEL, GROQ_STT_MODEL, TEMPERATURE, MAX_TOKENS, MAX_RETRIES,
    VOICE, TTS_FFMPEG_FILTERS, TTS_SEND_CHUNK_BYTES, SENTENCE_MIN_CHARS, TTS_TAIL_S,
    SERIAL_PORT, SERIAL_BAUD,
    SYSTEM_PROMPT, EXIT_PHRASES,
)


FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
console = Console()


# =========================
# Conversiones de audio
# =========================

def uint8_to_int16_bytes(uint8_buf: bytes) -> bytes:
    """uint8 (silencio=128) -> int16 PCM little-endian centrado en 0."""
    arr = np.frombuffer(uint8_buf, dtype=np.uint8).astype(np.int16)
    arr = (arr - 128) * 256
    return arr.tobytes()


def rms_uint8(uint8_buf: bytes) -> float:
    """RMS de un frame uint8 ya centrado en 128."""
    arr = np.frombuffer(uint8_buf, dtype=np.uint8).astype(np.float32) - 128.0
    if arr.size == 0:
        return 0.0
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
# Wake word helpers
# =========================

def contains_wake_word(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in WAKE_WORDS)


def strip_wake_word(text: str) -> str:
    low = text.lower()
    for w in sorted(WAKE_WORDS, key=len, reverse=True):
        idx = low.find(w)
        if idx >= 0:
            return (text[:idx] + text[idx + len(w):]).strip(" ,.:;-¿?¡!")
    return text


def is_exit(text: str) -> bool:
    cleaned = text.lower().strip(" .,!?¿¡")
    return cleaned in EXIT_PHRASES


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
        # Serial al ESP32 para los comandos ESTADO (OLED). Si el puerto está
        # ocupado o no existe, queda en no-op silencioso.
        self.serial = RobotSerial(SERIAL_PORT, SERIAL_BAUD) if SERIAL_PORT else None
        if self.serial and self.serial.connected:
            console.print(f"[dim]Serial al ESP32 abierto en {SERIAL_PORT}[/]")
        else:
            console.print(f"[dim]Serial al ESP32 no disponible (OK, sigue sin OLED)[/]")
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
        Endpointing con gate adaptativo de ruido:
          - webrtcvad da spectral matching (es voz humana?)
          - RMS gate sobre un piso de ruido auto-aprendido descarta el ruido
            ambiente de fondo (gente lejos, ventilador, click esporádico)
          - Arranca solo tras SPEECH_START_FRAMES consecutivos => sin falsos
            disparos por un solo click
          - Termina tras SILENCE_END_MS sin voz real => no espera por ruido
        """
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
                    # mientras estemos en "silencio aparente", el piso puede
                    # seguir subiendo si el ambiente se puso ruidoso
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

    # ----- STT -----

    def transcribe(self, uint8_audio: bytes) -> str:
        wav_bytes = uint8_frames_to_wav_bytes(uint8_audio)
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

    # ----- speak (HALF-DUPLEX, sin barge-in) -----

    def speak(self, audio_u8: bytes) -> Optional[bytes]:
        """
        Half-duplex: mandamos UN solo mensaje (header con el length total)
        y luego el body en chunks paciendo a tasa real-time. El ESP32 lo
        toca de corrido sin volver a LISTEN entre chunks => audio fluido.

        Devuelve None siempre (compat con la cola de streaming).
        """
        if not audio_u8:
            return None

        total = len(audio_u8)
        bytes_per_sec = SAMPLE_RATE
        chunk_size = TTS_SEND_CHUNK_BYTES

        # 1) Abrir el mensaje grande con un único header (4 bytes BE)
        if not self.io.send_audio_header(total):
            return None

        # 2) Pre-fill: mandamos el primer colchón sin pacing para que el
        #    ESP32 arranque de inmediato y su TCP buffer se llene.
        prefill = min(4096, total)
        if not self.io.send_audio_body(audio_u8[:prefill]):
            return None
        sent = prefill
        start = time.monotonic()

        # 3) Pacing real-time: a partir de aquí mantenemos al ESP32 alimentado
        #    sin saturar TCP. Bytes ya enviados ≈ (elapsed * SAMPLE_RATE) + chunk.
        while sent < total:
            elapsed = time.monotonic() - start
            target = int(elapsed * bytes_per_sec) + chunk_size
            if sent < target:
                end = min(sent + chunk_size, total)
                if not self.io.send_audio_body(audio_u8[sent:end]):
                    return None
                sent = end
            else:
                time.sleep(0.01)

        # 4) Esperar a que el ESP32 termine de reproducir
        expected_duration = total / bytes_per_sec
        elapsed = time.monotonic() - start
        if elapsed < expected_duration + TTS_TAIL_S:
            time.sleep(expected_duration + TTS_TAIL_S - elapsed)

        return None

    def say(self, text: str) -> Optional[bytes]:
        """Atajo: sintetiza un texto y lo reproduce con barge-in."""
        try:
            audio = synthesize_to_u8(text)
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
                    audio = synthesize_to_u8(sent)
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
            f"Wake word: [cyan]{'ON' if WAKE_WORD_ENABLED else 'OFF'}[/]  •  "
            f"Barge-in: [cyan]{'ON' if BARGE_IN_ENABLED else 'OFF'}[/]\n"
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

                # 3) Gate wake word
                if not self.awake:
                    if not contains_wake_word(text):
                        console.print(f"[dim]…ignorado: {text!r}[/]")
                        continue
                    self.awake = True
                    self.last_interaction = time.time()
                    payload = strip_wake_word(text)
                    console.print(f"[bold cyan]🧑 Tú:[/] {text}")
                    if not payload:
                        self.say("¿Sí?")
                        continue
                else:
                    payload = text
                    console.print(f"[bold cyan]🧑 Tú:[/] {text}")

                self.last_interaction = time.time()

                # 4) Salida
                if is_exit(payload):
                    self.say("Hasta luego.")
                    return

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

            except KeyboardInterrupt:
                raise
            except Exception:
                traceback.print_exc()
                time.sleep(0.3)
