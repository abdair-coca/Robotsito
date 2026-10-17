"""
audio_engine.py — Micrófono siempre abierto, Silero VAD y fin de frase adaptativo (Fase R2).

Antes, cada turno abría el micrófono de nuevo (330–780 ms medidos): si el usuario
contestaba rápido, se perdían sus primeras sílabas. Y el VAD (webrtcvad nivel 3 +
umbral de nivel fijo) tomaba como voz el 100 % del ruido blanco a −35 dB y del
zumbido de 50 Hz, mientras dejaba fuera la voz baja (exigía nivel > 1.5 %; la voz
calibrada a 1 m llega a 0.6 %).

Ahora un único stream queda abierto y llena un búfer circular de bloques de 32 ms,
cada uno con su probabilidad de voz (Silero VAD v6.2: 0 % de falsos positivos con
ese mismo ruido) y su nivel. Quien escucha lee el búfer desde un poco antes de
empezar (preroll), así nada se corta.

Fin de frase adaptativo: tras una pausa corta se lanza la transcripción en segundo
plano (`al_pausa`). Si el texto parece completo, la frase termina ya; si termina
en "de", "que", "y"… se espera una pausa más larga por si el usuario sigue. Si
vuelve a hablar, lo adelantado se descarta y la frase continúa.

Anti-eco: mientras Bob habla (y un margen después), los bloques quedan marcados y
nadie los toma como voz del usuario.

Modelo (gitignoreado, MIT): models/silero_vad_v6.2.onnx, de
https://raw.githubusercontent.com/snakers4/silero-vad/v6.2/src/silero_vad/data/silero_vad.onnx
(sha256 1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3). Si falta,
voice_pipeline graba como antes.
"""
from __future__ import annotations

import io
import os
import queue
import threading
import time
import wave
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

SR = 16000
BLOQUE = 512                      # muestras por bloque: lo que Silero pide a 16 kHz
MS_BLOQUE = BLOQUE * 1000 / SR    # 32 ms
MODELO_DEFAULT = os.path.join(os.path.dirname(__file__), 'models', 'silero_vad_v6.2.onnx')

UMBRAL_VOZ = 0.5          # prob. de Silero para empezar a contar voz
UMBRAL_SILENCIO = 0.35    # por debajo, silencio (histéresis recomendada por Silero)
INICIO_BLOQUES = 2        # bloques de voz seguidos para decidir que empezó a hablar
PREROLL_MS = 320          # audio previo al inicio detectado que se conserva
PAUSA_CORTA_MS = 450      # pausa que lanza la transcripción adelantada
PAUSA_LARGA_MS = 1200     # pausa máxima si la frase quedó incompleta
ECO_MARGEN_MS = 250       # tras hablar Bob, el eco de la sala sigue unos ms
MIN_VOZ_MS = 150          # menos que esto (clicks, golpes) no es una frase
SIN_DATOS_S = 2.0         # sin bloques nuevos este tiempo → el stream se cayó

# Palabras con las que una frase casi nunca termina: si el texto adelantado acaba
# así, el usuario probablemente sigue hablando ("pon música de… Queen").
CONECTORES = {
    'y', 'e', 'o', 'u', 'ni', 'de', 'del', 'que', 'pero', 'para', 'por', 'con', 'sin',
    'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas', 'mi', 'mis', 'tu', 'tus',
    'su', 'sus', 'en', 'a', 'al', 'como', 'cuando', 'si', 'porque', 'pues', 'entonces',
    'este', 'eh', 'em', 'mm', 'mmm', 'osea', 'sobre', 'hasta', 'desde', 'entre', 'muy',
}


def frase_completa(texto: str) -> bool:
    """¿El texto adelantado parece una frase terminada?"""
    from stt import palabras     # import tardío: stt no depende de este módulo
    t = (texto or '').strip()
    if not t:
        return True               # ruido: no hay nada que esperar
    if t.endswith((',', '…', '...', '-', ':')):
        return False
    ps = palabras(t)
    return bool(ps) and ps[-1] not in CONECTORES


class SileroVAD:
    """Silero VAD (ONNX, MIT) con onnxruntime, un hilo. prob(bloque de 512 float32)."""

    def __init__(self, modelo: str = MODELO_DEFAULT):
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1
        self._s = ort.InferenceSession(modelo, so, providers=['CPUExecutionProvider'])
        self._sr = np.array(SR, np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), np.float32)
        self._ctx = np.zeros((1, 64), np.float32)

    def prob(self, bloque: np.ndarray) -> float:
        x = np.concatenate([self._ctx, bloque.reshape(1, -1).astype(np.float32)], axis=1)
        out, self._state = self._s.run(None, {'input': x, 'state': self._state, 'sr': self._sr})
        self._ctx = x[:, -64:]
        return float(out[0, 0])


@dataclass
class _Bloque:
    n: int                 # índice global
    audio: np.ndarray      # int16 (512)
    prob: float            # Silero
    nivel: float           # RMS en % del fondo de escala (misma escala que calibrar_mic)
    bob: bool              # Bob hablaba (o su eco): no es voz del usuario


@dataclass
class Frase:
    audio: np.ndarray                     # int16 mono 16 kHz
    segundos_voz: float
    adelantado: Optional[Future] = None   # transcripción lanzada en la pausa (R2)
    _wav: Optional[bytes] = None

    def wav(self) -> bytes:
        if self._wav is None:
            self._wav = a_wav(self.audio)
        return self._wav


def a_wav(audio_i16: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(audio_i16.astype(np.int16).tobytes())
    return buf.getvalue()


def _ms(ms: float) -> int:
    return max(1, int(round(ms / MS_BLOQUE)))


class Microfono:
    """
    Micrófono de la laptop siempre abierto. `vad`: SileroVAD (o algo con .prob).
    `dispositivo`: índice de sounddevice (None = default). `abrir=False` no toca el
    hardware (tests): el audio entra con alimentar().
    """

    def __init__(self, vad, dispositivo=None, segundos_buffer: float = 30.0,
                 log: Callable[[str], None] = print, abrir: bool = True):
        self._vad = vad
        self._dispositivo = dispositivo
        self._log = log
        self._bloques: deque = deque(maxlen=int(segundos_buffer * 1000 / MS_BLOQUE))
        self._n = 0
        self._cond = threading.Condition()
        self._resto = np.zeros(0, np.int16)
        self._bob_hablando = False
        self._eco_hasta = 0
        self._ultimo_bob = -1
        self._ultimo_dato = time.monotonic()
        self._q: queue.Queue = queue.Queue()
        self._stream = None
        self._detener = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='stt-adelantado')
        if abrir:
            self._abrir()
            threading.Thread(target=self._trabajar, daemon=True, name='mic-continuo').start()

    # ── Captura ───────────────────────────────────────────────────────────────

    def _abrir(self) -> None:
        import sounddevice as sd

        def callback(indata, frames, t, status):
            self._q.put(bytes(indata))     # lo mínimo en el hilo de audio
        try:
            self._stream = sd.RawInputStream(device=self._dispositivo, samplerate=SR, channels=1,
                                             dtype='int16', blocksize=BLOQUE, callback=callback)
            self._stream.start()
            self._ultimo_dato = time.monotonic()
        except Exception as e:
            self._stream = None
            self._log(f'[red][mic] no se pudo abrir el micrófono: {e}[/]')

    def _trabajar(self) -> None:
        while not self._detener.is_set():
            try:
                datos = self._q.get(timeout=0.5)
            except queue.Empty:
                if time.monotonic() - self._ultimo_dato > SIN_DATOS_S:
                    self._log('[yellow][mic] sin audio del micrófono → reabriendo[/]')
                    self._cerrar_stream()
                    self._abrir()
                continue
            self._ultimo_dato = time.monotonic()
            self.alimentar(np.frombuffer(datos, np.int16))

    def alimentar(self, muestras: np.ndarray) -> None:
        """Agrega audio int16 al búfer, en bloques de 32 ms con su VAD y nivel."""
        x = np.concatenate([self._resto, muestras.astype(np.int16)])
        nuevos = []
        while len(x) >= BLOQUE:
            b, x = x[:BLOQUE], x[BLOQUE:]
            f = b.astype(np.float32) / 32768.0
            nuevos.append((b, self._vad.prob(f), float(np.sqrt(np.mean(f * f))) * 100.0))
        self._resto = x
        if not nuevos:
            return
        with self._cond:
            for b, p, nivel in nuevos:
                bob = self._bob_hablando or self._n < self._eco_hasta
                if bob:
                    self._ultimo_bob = self._n
                self._bloques.append(_Bloque(self._n, b, p, nivel, bob))
                self._n += 1
            self._cond.notify_all()

    def bob_habla(self, activo: bool) -> None:
        """Llamar al empezar y al terminar cada audio de Bob (anti-eco)."""
        with self._cond:
            self._bob_hablando = activo
            if not activo:
                self._eco_hasta = self._n + _ms(ECO_MARGEN_MS)

    def _cerrar_stream(self) -> None:
        s, self._stream = self._stream, None
        if s is not None:
            try:
                s.stop()
                s.close()
            except Exception:
                pass

    def cerrar(self) -> None:
        self._detener.set()
        self._cerrar_stream()
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._cond:
            self._cond.notify_all()

    @property
    def abierto(self) -> bool:
        return self._stream is not None

    # ── Escucha ───────────────────────────────────────────────────────────────

    def _bloque(self, n: int, cancelar: Optional[threading.Event]) -> Optional[_Bloque]:
        """Bloque n (espera a que llegue). None si se cancela o el audio se cortó."""
        with self._cond:
            while self._n <= n:
                if self._detener.is_set() or (cancelar is not None and cancelar.is_set()):
                    return None
                if not self._cond.wait(timeout=SIN_DATOS_S + 0.5) and self._n <= n:
                    return None
            primero = self._bloques[0].n
            if n < primero:
                return self._bloques[0]
            return self._bloques[n - primero]

    def escuchar(self, timeout_inicial: float = 8.0, max_s: float = 12.0,
                 silencio_fin_ms: float = 500.0, nivel_min: float = 0.0,
                 al_pausa: Optional[Callable[[bytes], str]] = None,
                 pausa_corta_ms: float = PAUSA_CORTA_MS, pausa_larga_ms: float = PAUSA_LARGA_MS,
                 invitar: Optional[Callable[[], None]] = None, invitar_s: float = 3.0,
                 cancelar: Optional[threading.Event] = None) -> Optional[Frase]:
        """
        Espera una frase. None si no empezó a hablar en `timeout_inicial` s (o se canceló).
        nivel_min: nivel mínimo (RMS %) para que el inicio cuente (filtra voces lejanas).
        al_pausa(wav) → texto (o una tupla cuyo primer elemento es el texto):
            transcripción adelantada en la pausa corta; si el texto parece completo la
            frase termina ahí, si no se espera hasta `pausa_larga_ms`.
            Sin al_pausa, la frase termina con `silencio_fin_ms` de silencio.
        """
        with self._cond:
            inicio_escucha = self._n
            desde = max(self._n - _ms(PREROLL_MS), self._ultimo_bob + 1,
                        self._bloques[0].n if self._bloques else 0)
        n = desde
        timeout_b = _ms(timeout_inicial * 1000)
        invitar_b = _ms(invitar_s * 1000)
        invitado = False

        # ── Esperar el inicio ──
        seguidos = 0
        while True:
            b = self._bloque(n, cancelar)
            if b is None:
                return None
            voz = (not b.bob) and b.prob >= UMBRAL_VOZ and b.nivel >= nivel_min
            seguidos = seguidos + 1 if voz else 0
            if seguidos >= INICIO_BLOQUES:
                inicio = max(n - seguidos + 1 - _ms(PREROLL_MS), desde, self._ultimo_bob + 1)
                frase = self._seguir_frase(inicio, n + 1, max_s, silencio_fin_ms, al_pausa,
                                           pausa_corta_ms, pausa_larga_ms, cancelar)
                if frase is None:
                    return None
                if frase.segundos_voz * 1000 >= MIN_VOZ_MS:
                    return frase
                seguidos = 0                  # un golpe, no una frase: seguir esperando
                n = inicio + int(round(len(frase.audio) / BLOQUE))
                continue
            if invitar and not invitado and n - inicio_escucha >= invitar_b:
                invitado = True
                invitar()
            if n - inicio_escucha >= timeout_b:
                return None
            n += 1

    def _seguir_frase(self, inicio: int, n: int, max_s, silencio_fin_ms, al_pausa,
                      pausa_corta_ms, pausa_larga_ms, cancelar) -> Optional[Frase]:
        partes = []
        for k in range(inicio, n):
            b = self._bloque(k, cancelar)
            if b is None:
                return None
            partes.append(b.audio)
        voz_b = INICIO_BLOQUES
        silencio = 0
        max_b = _ms(max_s * 1000)
        corta_b, larga_b, fin_b = _ms(pausa_corta_ms), _ms(pausa_larga_ms), _ms(silencio_fin_ms)
        adelantado: Optional[Future] = None
        wav_adelantado: Optional[bytes] = None
        corte = None                       # nº de bloques de `partes` enviados a transcribir

        while True:
            b = self._bloque(n, cancelar)
            if b is None:
                return None
            n += 1
            partes.append(b.audio)
            if (not b.bob) and b.prob >= UMBRAL_SILENCIO:
                voz_b += 1
                silencio = 0
                if adelantado is not None:  # siguió hablando: lo adelantado no sirve
                    adelantado.cancel()
                    adelantado, wav_adelantado, corte = None, None, None
            else:
                silencio += 1

            if len(partes) >= max_b:
                break
            if al_pausa is None:
                if silencio >= fin_b:
                    break
                continue
            if silencio == corta_b and adelantado is None and voz_b * MS_BLOQUE >= MIN_VOZ_MS:
                corte = len(partes)
                wav_adelantado = a_wav(np.concatenate(partes))
                adelantado = self._pool.submit(al_pausa, wav_adelantado)
            if adelantado is not None and adelantado.done():
                texto = '' if adelantado.cancelled() or adelantado.exception() else adelantado.result()
                if frase_completa(texto if isinstance(texto, str) else texto[0]):
                    break
            if silencio >= larga_b:
                break

        if adelantado is not None and corte is not None:
            audio = np.concatenate(partes[:corte])
            return Frase(audio, voz_b * MS_BLOQUE / 1000.0, adelantado, wav_adelantado)
        return Frase(np.concatenate(partes), voz_b * MS_BLOQUE / 1000.0)
