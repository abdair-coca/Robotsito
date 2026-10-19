"""Síntesis de voz: Piper, edge-tts, empaquetado WAV."""
from __future__ import annotations

import io as _io
import asyncio
import subprocess
import threading
import wave
from typing import Optional
import edge_tts
import imageio_ffmpeg
from config import VOICE, TTS_BACKEND, PIPER_VOICE, PIPER_LENGTH_SCALE, PIPER_PITCH_FACTOR, PIPER_FFMPEG_FILTERS
from voz.pipeline.emocion import _prosody_for_emo


FFMPEG  = imageio_ffmpeg.get_ffmpeg_exe()


_SALIDA_SR = 24000   # frecuencia del mixer de pygame (VoicePipeline.__init__)


# Piper (local, CPU): la voz se carga una vez y se sintetiza con lock, porque
# el TTS se llama desde varios hilos (charla, soliloquio, recordatorios).
_PIPER = None


_PIPER_LOCK = threading.Lock()


def _piper_audio(text: str) -> bytes:
    global _PIPER
    with _PIPER_LOCK:
        if _PIPER is None:
            from piper import PiperVoice
            _PIPER = PiperVoice.load(PIPER_VOICE)
        from piper import SynthesisConfig
        chunks = list(_PIPER.synthesize(
            text, syn_config=SynthesisConfig(length_scale=PIPER_LENGTH_SCALE)))
    if not chunks:
        return b''
    pcm = b''.join(c.audio_int16_bytes for c in chunks)
    sr = chunks[0].sample_rate
    # Tono: asetrate sube pitch y velocidad; aresample+atempo devuelven la velocidad.
    # Luego el EQ/compresor de carácter. PCM s16 → mp3 para que pygame lo reproduzca.
    f = PIPER_PITCH_FACTOR
    partes = []
    if f != 1.0:
        partes.append(f"asetrate={sr}*{f},aresample={sr},atempo={1/f:.5f}")
    if PIPER_FFMPEG_FILTERS:
        partes.append(PIPER_FFMPEG_FILTERS)
    cmd = [FFMPEG, '-hide_banner', '-loglevel', 'error',
           '-f', 's16le', '-ar', str(sr), '-ac', '1', '-i', 'pipe:0']
    if partes:
        cmd += ['-af', ','.join(partes)]
    if not TTS_PCM_ENABLED:
        cmd += ['-f', 'mp3', 'pipe:1']
        return subprocess.run(cmd, input=pcm, capture_output=True, check=False).stdout
    # R4: PCM a la frecuencia del mixer, en un WAV en memoria (sin codificar a mp3,
    # sin archivo temporal): ~80 ms menos por frase, medido.
    cmd += ['-ar', str(_SALIDA_SR), '-f', 's16le', 'pipe:1']
    salida = subprocess.run(cmd, input=pcm, capture_output=True, check=False).stdout
    return _pcm_a_wav(salida, _SALIDA_SR) if salida else b''


def _pcm_a_wav(pcm: bytes, sr: int) -> bytes:
    buf = _io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return buf.getvalue()


# Fase R4: interacción. Guardado por si un config viejo no lo define.
#   TTS_PCM_ENABLED       Piper → WAV en memoria (sin mp3 ni archivo temporal)
#   TONO_ESCUCHA_ENABLED  "tic" corto y suave cuando Bob empieza a escuchar tu respuesta
#   RELLENO_ENABLED       si la respuesta tarda más de RELLENO_S, Bob dice "Mmm, a ver…"
try:
    from config import TTS_PCM_ENABLED
except Exception:
    TTS_PCM_ENABLED = True


async def _edge_tts_bytes(text: str, rate: str, pitch: str) -> bytes:
    chunks = []
    async for c in edge_tts.Communicate(text, voice=VOICE, rate=rate, pitch=pitch).stream():
        if c['type'] == 'audio':
            chunks.append(c['data'])
    return b''.join(chunks)


def _sintetizar(text: str, emo: Optional[str] = None) -> bytes:
    """Audio de `text`: WAV en memoria (Piper, R4) o mp3 (edge-tts)."""
    if not text.strip():
        return b''
    if TTS_BACKEND == 'piper':
        return _piper_audio(text)
    rate, pitch = _prosody_for_emo(emo)
    return asyncio.run(_edge_tts_bytes(text, rate, pitch))
