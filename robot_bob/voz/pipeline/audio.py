"""Utilidades de audio: conversión uint8/int16, WAV, RMS, filtro y dispositivo de mic."""
from __future__ import annotations

import io as _io
import wave
from typing import Optional
import numpy as np
import sounddevice as sd
from scipy.signal import butter, sosfilt
from config import SAMPLE_RATE


# ── Constantes locales ─────────────────────────────────────────────────────────
_LAP_SAMPLE_RATE = 16000  # sounddevice graba a 16 kHz


def _segundos_wav(wav: Optional[bytes]) -> float:
    """Duración de un WAV en segundos (0 si no hay audio o no es WAV)."""
    if not wav:
        return 0.0
    try:
        with wave.open(_io.BytesIO(wav)) as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


def _mic_device():
    """Índice del mic Intel por DirectSound. El default de Windows (MME) en la
    laptop captó casi nada; DirectSound a 16 kHz sí captura la voz.
    None = dispositivo default del sistema (si no encuentra el Intel)."""
    try:
        for i, d in enumerate(sd.query_devices()):
            api = sd.query_hostapis(d['hostapi'])['name']
            if (d['max_input_channels'] > 0 and 'Intel' in d['name']
                    and api == 'Windows DirectSound'):
                return i
    except Exception:
        pass
    return None


# Filtro pasa-banda de voz (pre-calculado, reutilizable)
_VOICE_BANDPASS = butter(4, [80, 3500], btype='band', fs=SAMPLE_RATE, output='sos')


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
