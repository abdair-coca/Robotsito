"""
audio_helpers.py — Funciones puras de audio y texto para el pipeline de voz.
Sin dependencias del estado de VoicePipeline.
"""

from __future__ import annotations

import io as _io
import re
import subprocess
import asyncio
import wave
from typing import Optional

import numpy as np
import edge_tts
from scipy.signal import butter, sosfilt

from config import (
    SAMPLE_RATE, VOICE, TTS_FFMPEG_FILTERS, TTS_TAIL_S,
    TTS_RATE_BASE, TTS_PITCH_BASE, TTS_EMO_PROSODY,
    SENTENCE_MIN_CHARS, EXIT_PHRASES, GOODBYE_PHRASES,
)

FFMPEG = __import__('imageio_ffmpeg', fromlist=['get_ffmpeg_exe']).get_ffmpeg_exe()

_EMO_TAG_RE = re.compile(r'\[EMO:([A-ZÁÉÍÓÚÜÑ_]+)\]', re.IGNORECASE)
_VALID_EMOS = {'FELIZ', 'MUY_FELIZ', 'EMOCIONADO', 'CURIOSO', 'TRAVIESO',
               'PENSANDO', 'SORPRENDIDO', 'ASUSTADO', 'CONFUNDIDO', 'AVERGONZADO',
               'TRISTE', 'MUY_TRISTE', 'ENOJADO', 'SOSPECHANDO', 'ORGULLOSO',
               'AMOR', 'HABLANDO'}

_VOICE_BANDPASS = butter(4, [80, 3500], btype='band', fs=SAMPLE_RATE, output='sos')


def extract_emo_tag(text: str) -> tuple:
    emo = None
    for m in _EMO_TAG_RE.finditer(text):
        cand = m.group(1).upper()
        if emo is None and cand in _VALID_EMOS:
            emo = cand
    clean = _EMO_TAG_RE.sub('', text).strip()
    return emo, clean


def ensure_emo_tag(sent: str) -> str:
    for m in _EMO_TAG_RE.finditer(sent):
        if m.group(1).upper() in _VALID_EMOS:
            return sent
    return '[EMO:HABLANDO] ' + sent


def rms_uint8(buf: bytes) -> float:
    a = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
    if not a.size:
        return 0.0
    a = a - a.mean()
    return float(np.sqrt(np.mean(a * a)))


def uint8_to_int16_bytes(buf: bytes) -> bytes:
    a = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
    if not a.size:
        return b''
    a = (a - a.mean()) * 256.0
    return np.clip(a, -32768, 32767).astype(np.int16).tobytes()


def uint8_to_wav(uint8_audio: bytes) -> bytes:
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


_HAPPY_WORDS = {
    'bien', 'genial', 'excelente', 'perfecto', 'fantástico', 'claro', 'por supuesto',
    'feliz', 'alegre', 'encantado', 'maravilloso', 'increíble', 'buenísimo',
}


def is_happy(text: str) -> bool:
    words = set(text.lower().split())
    return bool(words & _HAPPY_WORDS)


_ALUCINACIONES = {
    'gracias', 'muchas gracias', 'gracias por ver el video', 'gracias por ver',
    'no', 'si', 'ya', 'eh', 'ah', 'mmm', 'chau', 'ok', 'okay', 'a', 'y', 'the',
}


def es_alucinacion(texto: str) -> bool:
    t = texto.lower().strip(' .,!?¿¡')
    if not t:
        return True
    if 'amara' in t or 'subtitul' in t or 'suscrib' in t:
        return True
    return t in _ALUCINACIONES


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


def intent_giro(texto: str) -> Optional[str]:
    t = texto.lower()
    if any(k in t for k in _GIRO_DER_KW):
        return 'derecha'
    if any(k in t for k in _GIRO_IZQ_KW):
        return 'izquierda'
    if any(k in t for k in _GIRO_BUSCAR_KW):
        return 'buscar'
    return None


_RE_NOMBRE = re.compile(
    r'\b(?:me llamo|mi nombre es|me dicen|soy)\s+([a-záéíóúñ]{2,20})', re.IGNORECASE)
_NO_NOMBRE = {'de', 'un', 'una', 'el', 'la', 'muy', 'yo', 'estudiante', 'ingeniero',
              'ingeniera', 'profe', 'profesor', 'doctor', 'el', 'tu', 'su', 'que',
              'bien', 'mal', 'feliz', 'triste', 'de', 'del', 'para', 'medio'}


def extraer_nombre(texto: str) -> Optional[str]:
    m = _RE_NOMBRE.search(texto)
    if not m:
        return None
    n = m.group(1).strip()
    if n.lower() in _NO_NOMBRE:
        return None
    return n.capitalize()


def is_exit(text: str) -> bool:
    return text.lower().strip(' .,!?¿¡') in EXIT_PHRASES


def is_goodbye(text: str) -> bool:
    return text.lower().strip(' .,!?¿¡') in GOODBYE_PHRASES


def prosody_for_emo(emo: Optional[str]) -> tuple:
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


def synthesize_mp3(text: str, emo: Optional[str] = None) -> bytes:
    if not text.strip():
        return b''
    rate, pitch = prosody_for_emo(emo)
    return asyncio.run(_edge_tts_bytes(text, rate, pitch))


def mp3_to_wav(mp3: bytes) -> bytes:
    proc = subprocess.run(
        [FFMPEG, '-hide_banner', '-loglevel', 'error',
         '-i', 'pipe:0',
         '-af', TTS_FFMPEG_FILTERS,
         '-ar', str(SAMPLE_RATE), '-ac', '1',
         '-acodec', 'pcm_u8', '-f', 'u8', 'pipe:1'],
        input=mp3, capture_output=True, check=False,
    )
    return proc.stdout


_SENT_RE = re.compile(r'[.!?¡¿…\n]+[\s"\')\]]*')


def split_sentence(buf: str) -> tuple:
    if len(buf) < SENTENCE_MIN_CHARS:
        return None, buf
    m = _SENT_RE.search(buf, SENTENCE_MIN_CHARS - 1)
    if m is None:
        return None, buf
    return buf[:m.end()].strip(), buf[m.end():]
