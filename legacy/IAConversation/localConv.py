import os
import io
import time
import wave
import queue
import asyncio
import tempfile
import threading
import traceback
from collections import deque
from typing import Optional

import numpy as np
import sounddevice as sd
import webrtcvad
import edge_tts
import pygame
from groq import Groq
from rich.console import Console
from rich.panel import Panel

from config import GROQ_API_KEY


# =========================
# CONFIG
# =========================

# --- Voz TTS (cámbiala libremente) ---
VOICE = "es-MX-DaliaNeural"
# Alternativas:
#   "es-MX-JorgeNeural"   (masculina mexicana)
#   "es-ES-AlvaroNeural"  (masculina España)
#   "es-AR-ElenaNeural"   (femenina Argentina)

# --- Wake word ---
WAKE_WORD_ENABLED = True           # True para requerir "Creeper" antes de hablar
WAKE_WORDS = ("creeper", "hey creeper", "oye creeper")
CONVERSATION_TIMEOUT_S = 5.0       # tras esto sin hablar, re-arma el wake word

# --- Audio / VAD ---
SAMPLE_RATE = 16000
FRAME_MS = 30                       # webrtcvad: 10, 20 o 30
FRAME_SIZE = int(SAMPLE_RATE * FRAME_MS / 1000)   # 480 muestras
VAD_AGGRESSIVENESS = 2              # 0..3
SILENCE_END_MS = 800
MAX_RECORDING_S = 15
MIN_SPEECH_S = 0.3
PREROLL_FRAMES = 10                 # frames a conservar antes del primer habla

# --- Barge-in ---
BARGE_IN_ENABLED = True
BARGE_IN_RMS = 0.02                 # subir si hay eco/falsos positivos, bajar si no detecta tu voz
BARGE_IN_SUSTAINED_MS = 300         # ms de voz sostenida para considerar que estás interrumpiendo
BARGE_IN_SETTLE_MS = 500            # ignora el mic durante este tiempo al empezar TTS (evita eco inicial)

# --- LLM / STT ---
GROQ_LLM_MODEL = "llama-3.3-70b-versatile"
GROQ_STT_MODEL = "whisper-large-v3-turbo"
TEMPERATURE = 0.8
MAX_RETRIES = 2

SYSTEM_PROMPT = """
Eres Creeper, un robot amigable creado por Abdair.
Responde de forma natural, breve y conversacional.
Mantén respuestas de máximo 2 o 3 frases.
""".strip()

EXIT_PHRASES = {"salir", "terminar", "adiós", "adios", "chao", "hasta luego"}


# =========================
# ESTADO GLOBAL
# =========================

console = Console()
client = Groq(api_key=GROQ_API_KEY)
pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)

conversation: list[dict] = []           # historial completo (user/assistant)
stop_tts_event = threading.Event()


# =========================
# HELPERS DE AUDIO
# =========================

def float_to_pcm16_bytes(frame: np.ndarray) -> bytes:
    return (np.clip(frame, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def rms(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))


def pcm_to_wav_bytes(pcm_chunks: list[bytes]) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(b"".join(pcm_chunks))
    return buf.getvalue()


# =========================
# GRABACIÓN CON VAD
# =========================

def record_until_silence(initial_timeout_s: Optional[float]) -> Optional[bytes]:
    """
    Espera hasta initial_timeout_s segundos a que empiece la voz.
    Tras detectar voz, corta cuando hay SILENCE_END_MS de silencio.
    Devuelve WAV bytes o None si no hubo voz.
    """
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    silence_frames_needed = SILENCE_END_MS // FRAME_MS
    max_frames = (MAX_RECORDING_S * 1000) // FRAME_MS

    preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
    recorded: list[bytes] = []
    speech_started = False
    silence_streak = 0
    waited_ms = 0
    speech_ms = 0
    q: queue.Queue = queue.Queue()

    def cb(indata, frames, time_info, status):
        q.put(indata.copy())

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=FRAME_SIZE,
        callback=cb,
    ):
        while True:
            try:
                frame = q.get(timeout=1.0).flatten()
            except queue.Empty:
                continue

            pcm16 = float_to_pcm16_bytes(frame)
            is_speech = vad.is_speech(pcm16, SAMPLE_RATE)

            if not speech_started:
                preroll.append(pcm16)
                if is_speech:
                    speech_started = True
                    recorded.extend(preroll)
                    speech_ms = FRAME_MS * len(preroll)
                else:
                    waited_ms += FRAME_MS
                    if initial_timeout_s is not None and waited_ms / 1000.0 >= initial_timeout_s:
                        return None
            else:
                recorded.append(pcm16)
                speech_ms += FRAME_MS
                if is_speech:
                    silence_streak = 0
                else:
                    silence_streak += 1
                    if silence_streak >= silence_frames_needed:
                        break
                if len(recorded) >= max_frames:
                    break

    if not speech_started or speech_ms / 1000.0 < MIN_SPEECH_S:
        return None

    if silence_streak > 0:
        recorded = recorded[: -silence_streak]

    return pcm_to_wav_bytes(recorded)


# =========================
# TRANSCRIPCIÓN (Groq Whisper)
# =========================

def transcribe(wav_bytes: bytes) -> str:
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.audio.transcriptions.create(
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


# =========================
# LLM (Groq) con historial
# =========================

def ask_groq(user_text: str) -> str:
    conversation.append({"role": "user", "content": user_text})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + conversation

    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = client.chat.completions.create(
                model=GROQ_LLM_MODEL,
                messages=messages,
                temperature=TEMPERATURE,
            )
            reply = (resp.choices[0].message.content or "").strip()
            conversation.append({"role": "assistant", "content": reply})
            return reply
        except Exception as e:
            last_exc = e
            time.sleep(0.4 * (attempt + 1))

    conversation.pop()   # revertir el user que metimos optimistamente
    raise last_exc  # type: ignore[misc]


# =========================
# TTS + reproducción con barge-in
# =========================

async def _synthesize_to(path: str, text: str):
    await edge_tts.Communicate(text, voice=VOICE).save(path)


def synthesize_mp3(text: str) -> str:
    f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    f.close()
    asyncio.run(_synthesize_to(f.name, text))
    return f.name


def _barge_in_listener(captured: list[Optional[bytes]]):
    """
    Escucha el mic mientras Creeper habla. Usa VAD + RMS para detectar voz real
    (no el eco del altavoz). Si detecta interrupción:
      1. Dispara stop_tts_event para callar al robot.
      2. Sigue grabando hasta silencio.
      3. Guarda el WAV resultante en captured[0].

    Ignora el mic durante BARGE_IN_SETTLE_MS al inicio del TTS para evitar
    que el arranque del audio del parlante dispare falsos positivos.
    """
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    sustained_needed = max(1, BARGE_IN_SUSTAINED_MS // FRAME_MS)
    silence_frames_needed = SILENCE_END_MS // FRAME_MS
    settle_frames = BARGE_IN_SETTLE_MS // FRAME_MS
    max_frames = (MAX_RECORDING_S * 1000) // FRAME_MS

    sustained = 0
    frames_seen = 0
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
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=FRAME_SIZE,
            callback=cb,
        ):
            while True:
                # si TTS terminó y no hubo barge-in, salimos
                if not speech_started and not pygame.mixer.music.get_busy():
                    return
                if speech_started and len(recorded) >= max_frames:
                    break

                try:
                    frame = q.get(timeout=0.1).flatten()
                except queue.Empty:
                    continue

                frames_seen += 1
                pcm16 = float_to_pcm16_bytes(frame)

                # ignoramos el arranque del TTS para evitar falsos positivos por eco
                if pygame.mixer.music.get_busy() and frames_seen < settle_frames:
                    continue

                is_vad = vad.is_speech(pcm16, SAMPLE_RATE)
                level = rms(frame)

                if not speech_started:
                    preroll.append(pcm16)
                    # mientras TTS suena exigimos VAD + RMS (filtra eco);
                    # tras TTS basta con VAD (más sensible)
                    if pygame.mixer.music.get_busy():
                        looks_like_speech = is_vad and level > BARGE_IN_RMS
                    else:
                        looks_like_speech = is_vad

                    if looks_like_speech:
                        sustained += 1
                        if sustained >= sustained_needed:
                            speech_started = True
                            recorded.extend(preroll)
                            speech_ms = FRAME_MS * len(preroll)
                            stop_tts_event.set()
                    else:
                        sustained = max(0, sustained - 1)
                else:
                    recorded.append(pcm16)
                    speech_ms += FRAME_MS
                    if is_vad:
                        silence_streak = 0
                    else:
                        silence_streak += 1
                        if silence_streak >= silence_frames_needed:
                            break
    except Exception:
        # si el mic no se puede abrir, desactivamos barge-in este turno
        return

    if speech_started and speech_ms / 1000.0 >= MIN_SPEECH_S:
        if silence_streak > 0:
            recorded = recorded[: -silence_streak]
        captured[0] = pcm_to_wav_bytes(recorded)


def speak(text: str) -> Optional[bytes]:
    """
    Reproduce el texto. Si BARGE_IN_ENABLED y el usuario interrumpe,
    devuelve los bytes WAV de lo que dijo (para usar como siguiente turno).
    Devuelve None si terminó sin interrupción.
    """
    if not text:
        return None
    mp3_path = synthesize_mp3(text)
    stop_tts_event.clear()
    captured: list[Optional[bytes]] = [None]
    watcher: Optional[threading.Thread] = None

    try:
        pygame.mixer.music.load(mp3_path)
        pygame.mixer.music.play()

        if BARGE_IN_ENABLED:
            watcher = threading.Thread(target=_barge_in_listener, args=(captured,), daemon=True)
            watcher.start()

        while pygame.mixer.music.get_busy():
            if stop_tts_event.is_set():
                pygame.mixer.music.stop()
                break
            time.sleep(0.04)
    finally:
        try:
            pygame.mixer.music.unload()
        except Exception:
            pass
        if watcher is not None:
            # esperamos a que termine de capturar lo que el usuario dijo
            watcher.join(timeout=MAX_RECORDING_S + 1.0)
        try:
            os.remove(mp3_path)
        except OSError:
            pass

    return captured[0]


# =========================
# WAKE WORD
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


# =========================
# MAIN LOOP
# =========================

def is_exit(text: str) -> bool:
    cleaned = text.lower().strip(" .,!?¿¡")
    return cleaned in EXIT_PHRASES


def main():
    console.print(Panel.fit(
        f"[bold green]🤖 Creeper listo[/]\n"
        f"Voz: [cyan]{VOICE}[/]   "
        f"Wake word: [cyan]{'ON' if WAKE_WORD_ENABLED else 'OFF'}[/]   "
        f"Barge-in: [cyan]{'ON' if BARGE_IN_ENABLED else 'OFF'}[/]\n"
        f"[dim]Di 'adiós' para salir. Ctrl+C también funciona.[/]",
        title="Creeper", border_style="green",
    ))

    awake = not WAKE_WORD_ENABLED
    last_interaction = time.time()
    pending_wav: Optional[bytes] = None   # audio capturado durante un barge-in

    while True:
        try:
            # Re-armar wake word tras inactividad
            if WAKE_WORD_ENABLED and awake and (time.time() - last_interaction) > CONVERSATION_TIMEOUT_S:
                console.print("[dim]💤 Volviendo a modo wake word...[/]")
                awake = False

            # ---------- escuchar ----------
            if pending_wav is not None:
                # venimos de un barge-in: ya tenemos la voz del usuario, no grabamos
                wav = pending_wav
                pending_wav = None
            elif not awake:
                console.print("[dim]👂 Esperando 'Creeper'...[/]")
                wav = record_until_silence(initial_timeout_s=None)
            else:
                console.print("[bold]🎤 Escuchando...[/]")
                wav = record_until_silence(initial_timeout_s=CONVERSATION_TIMEOUT_S)
                if wav is None and WAKE_WORD_ENABLED:
                    awake = False
                    continue

            if wav is None:
                continue

            # ---------- transcribir ----------
            try:
                with console.status("[cyan]Transcribiendo...[/]", spinner="dots"):
                    text = transcribe(wav)
            except Exception:
                console.print("[red]✗ Error al transcribir.[/]")
                speak("No te escuché bien, ¿puedes repetir?")
                continue

            if not text:
                continue

            # ---------- wake word gating ----------
            if not awake:
                if not contains_wake_word(text):
                    console.print(f"[dim]…ignorado: {text!r}[/]")
                    continue
                awake = True
                last_interaction = time.time()
                payload = strip_wake_word(text)
                console.print(f"[bold cyan]🧑 Tú:[/] {text}")
                if not payload:
                    speak("¿Sí?")
                    continue
            else:
                payload = text
                console.print(f"[bold cyan]🧑 Tú:[/] {text}")

            last_interaction = time.time()

            # ---------- comandos de salida ----------
            if is_exit(payload):
                speak("Hasta luego.")
                break

            # ---------- LLM ----------
            try:
                with console.status("[magenta]🧠 Pensando...[/]", spinner="dots"):
                    reply = ask_groq(payload)
            except Exception:
                console.print("[red]✗ Sin respuesta de Groq tras reintentos.[/]")
                speak("No pude conectarme. Intenta de nuevo en un momento.")
                continue

            console.print(f"[bold green]🤖 Creeper:[/] {reply}")

            # ---------- TTS con barge-in ----------
            captured = speak(reply)
            if captured is not None:
                console.print("[yellow]↳ interrumpido[/]")
                pending_wav = captured        # se procesa en la siguiente iteración
                awake = True                  # mantenemos la conversación abierta
            last_interaction = time.time()

        except KeyboardInterrupt:
            break
        except Exception:
            traceback.print_exc()

    console.print("\n[dim]Programa finalizado.[/]")


if __name__ == "__main__":
    main()
