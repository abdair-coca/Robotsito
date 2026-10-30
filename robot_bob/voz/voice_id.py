"""
voice_id.py — Reconocimiento de hablante (IDENTIDAD por voz) con WeSpeaker.

Paralelo a face_id.py: da un EMBEDDING (vector de 256 dims, L2-normalizado) de
una frase, que luego se compara contra los guardados para saber si habla Abdair
(memory.py). Modelo: WeSpeaker ResNet34-LM en ONNX (VoxCeleb, 16 kHz,
CC-BY-4.0), corrido con onnxruntime. Sin PyTorch: el fbank Kaldi está en numpy.

Validado en EXP-1-001 (experimento archivado fuera del repo):
frases de 2 s o más; con 1 s el puntaje cae mucho.

Carga en un hilo de fondo; `listo` indica cuándo está disponible.
"""

import io
import os
import threading
import wave

import numpy as np

import errores
import rutas

_MODELO_DEFAULT = os.path.join(rutas.MODELOS_DIR, 'voxceleb_resnet34_LM.onnx')
try:
    from config import VOICE_ID_MODEL as _MODELO
except Exception:
    _MODELO = _MODELO_DEFAULT

SR        = 16000
MIN_VOZ_S = 0.5      # menos voz que esto: el embedding no sirve (y el modelo no acepta vacío)


# ── Fbank Kaldi (equivale a torchaudio.compliance.kaldi.fbank con los args de
#    WeSpeaker: 80 bandas, 25/10 ms, Hamming, dither 0, sin energía) ─────────────
_FRAME, _SHIFT, _NFFT, _NMEL = 400, 160, 512, 80


def _mel(f):
    return 1127.0 * np.log(1.0 + f / 700.0)


def _banco_mel() -> np.ndarray:
    lo, hi = _mel(20.0), _mel(SR / 2)
    delta = (hi - lo) / (_NMEL + 1)
    mel_bins = _mel((SR / _NFFT) * np.arange(_NFFT // 2))
    izq = lo + np.arange(_NMEL)[:, None] * delta
    cen, der = izq + delta, izq + 2 * delta
    sube = (mel_bins - izq) / (cen - izq)
    baja = (der - mel_bins) / (der - cen)
    banco = np.maximum(0.0, np.minimum(sube, baja))
    return np.pad(banco, ((0, 0), (0, 1)))           # columna de Nyquist en 0


_BANCO   = _banco_mel().astype(np.float32)
_VENTANA = (0.54 - 0.46 * np.cos(2 * np.pi * np.arange(_FRAME) / (_FRAME - 1))).astype(np.float32)


def fbank(audio_i16: np.ndarray) -> np.ndarray:
    """Audio int16 a 16 kHz → features (T, 80) con la media restada por banda."""
    x = audio_i16.astype(np.float32)                  # WeSpeaker escala a rango int16
    n = 1 + (len(x) - _FRAME) // _SHIFT
    if n < 1:
        return np.zeros((0, _NMEL), np.float32)
    idx = np.arange(_FRAME)[None, :] + _SHIFT * np.arange(n)[:, None]
    fr = x[idx]
    fr = fr - fr.mean(axis=1, keepdims=True)          # remove_dc_offset
    fr = np.concatenate([fr[:, :1] * (1 - 0.97), fr[:, 1:] - 0.97 * fr[:, :-1]], axis=1)
    fr = fr * _VENTANA
    pot = np.abs(np.fft.rfft(fr, n=_NFFT)) ** 2
    feats = np.log(np.maximum(pot @ _BANCO.T, np.finfo(np.float32).eps))
    return (feats - feats.mean(axis=0)).astype(np.float32)


def solo_voz(audio_i16: np.ndarray) -> np.ndarray:
    """Quita los silencios con webrtcvad (frames de 30 ms, con 2 frames de margen)."""
    import webrtcvad
    vad = webrtcvad.Vad(2)
    f = SR * 30 // 1000
    n = len(audio_i16) // f
    voz = [vad.is_speech(audio_i16[i * f:(i + 1) * f].tobytes(), SR) for i in range(n)]
    keep = np.zeros(n, bool)
    for i, v in enumerate(voz):
        if v:
            keep[max(0, i - 2):i + 3] = True
    partes = [audio_i16[i * f:(i + 1) * f] for i in range(n) if keep[i]]
    return np.concatenate(partes) if partes else np.zeros(0, np.int16)


def wav_a_i16(wav_bytes: bytes) -> np.ndarray:
    """WAV mono int16 (cualquier frecuencia) → int16 a 16 kHz."""
    with wave.open(io.BytesIO(wav_bytes), 'rb') as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError('se espera WAV mono de 16 bits')
        sr = w.getframerate()
        audio = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    if sr != SR:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(SR, sr)
        audio = resample_poly(audio.astype(np.float32), SR // g, sr // g)
        audio = audio.clip(-32768, 32767).astype(np.int16)
    return audio


class VoiceID:
    def __init__(self, modelo: str = _MODELO, en_fondo: bool = True):
        self._sesion = None
        self._lock   = threading.Lock()
        self._listo  = threading.Event()
        self._modelo = modelo
        if en_fondo:
            threading.Thread(target=self._cargar, daemon=True, name='voice-id-load').start()
        else:
            self._cargar()

    def _cargar(self) -> None:
        try:
            import onnxruntime as ort
            sesion = ort.InferenceSession(self._modelo, providers=['CPUExecutionProvider'])
            with self._lock:
                self._sesion = sesion
            self._listo.set()
            print('[voice_id] WeSpeaker listo (resnet34_LM).')
        except Exception as e:
            errores.error('IDENTIDAD', 'No cargó el modelo de voz: Bob no sabrá quién habla ni '
                          'obedecerá los comandos del dueño.', e)

    @property
    def listo(self) -> bool:
        return self._listo.is_set()

    def embedding(self, audio_i16: np.ndarray):
        """Embedding L2-normalizado np.float32[256] de audio int16 a 16 kHz YA sin
        silencios, o None si hay menos de MIN_VOZ_S de voz o no está listo."""
        sesion = self._sesion
        if sesion is None or len(audio_i16) < MIN_VOZ_S * SR:
            return None
        feats = fbank(audio_i16)[None]
        with self._lock:
            emb = sesion.run(['embs'], {'feats': feats})[0][0]
        return (emb / (np.linalg.norm(emb) + 1e-9)).astype(np.float32)

    def analizar(self, wav_bytes: bytes):
        """Devuelve (embedding L2-normalizado np.float32[256], segundos de voz) de
        una frase en WAV (lo que devuelve _grabar), o (None, segundos) si no hay
        voz suficiente, no está listo o el audio no se puede leer."""
        if self._sesion is None or not wav_bytes:
            return None, 0.0
        try:
            voz = solo_voz(wav_a_i16(wav_bytes))
        except Exception as e:
            errores.aviso('IDENTIDAD', 'No pude leer el audio para reconocer la voz; este turno '
                          'sigue sin ese dato.', e)
            return None, 0.0
        segundos = len(voz) / SR
        return self.embedding(voz), segundos
