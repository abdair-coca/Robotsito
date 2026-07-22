"""
Monitor de wake word. Corre en hilo propio, graba continuamente en background
mientras Bob no esta en conversacion.
"""

from __future__ import annotations

import io as _io
import queue
import threading
import time
import wave
from typing import Optional

import numpy as np
import sounddevice as sd
from rich.console import Console

from config import (
    USE_ROBOT_MIC,
    WAKE_WINDOW_S, WAKE_VOICE_LEVEL, WAKE_SILENCE_CUT_S,
    WAKE_MIN_LEVEL, FRAME_MS,
)
from audio_helpers import es_alucinacion, rms_uint8, uint8_to_wav
from expression_engine import pulse_emotion, EMO_WAKE_DETECTED, PULSE_FAST

console = Console()
_LAP_SAMPLE_RATE = 16000


class WakeMonitor:
    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True,
                         name='wake-monitor').start()

    def _loop(self) -> None:
        while not self._detener.is_set():
            if self._sm.en_conversacion:
                time.sleep(0.2)
                continue
            if self._muted.is_set():
                time.sleep(0.2)
                continue
            audio = self._grabar()
            if audio is None:
                continue
            texto = self._transcribir(audio)
            if not texto:
                continue
            if es_alucinacion(texto):
                continue
            ww = self._wake.detect(texto)
            if ww.detected:
                payload = (ww.payload or '').strip()
                if payload:
                    console.print(f'[bold yellow][wake] "{texto}" -> payload: "{payload}"[/]')
                else:
                    console.print(f'[bold yellow][wake] "{texto}" detectado![/]')
                pulse_emotion(self._serial, self._sm, EMO_WAKE_DETECTED, PULSE_FAST)
                self._sm.notificar_wake_word(payload=payload or None)
            else:
                console.print(f'[dim][wake?] heard: "{texto}"[/]')

    def _grabar(self) -> Optional[bytes]:
        if USE_ROBOT_MIC and self._audio_io is not None and self._audio_io.connected:
            return self._grabar_robot()
        return self._grabar_laptop()

    def _grabar_robot(self) -> Optional[bytes]:
        self._audio_io.drain_mic()
        collected = bytearray()
        t0 = time.monotonic()
        t_ultima_voz = 0.0
        while time.monotonic() - t0 < WAKE_WINDOW_S and not self._detener.is_set():
            if self._sm.en_conversacion:
                return None
            chunk = self._audio_io.get_mic(timeout=0.1)
            if chunk:
                collected.extend(chunk)
                ahora = time.monotonic()
                if rms_uint8(bytes(chunk)) >= 8.0:
                    t_ultima_voz = ahora
                elif t_ultima_voz and ahora - t_ultima_voz >= WAKE_SILENCE_CUT_S:
                    break
        if not collected:
            return None
        raw = bytes(collected)
        if rms_uint8(raw) < 4.0:
            return None
        return uint8_to_wav(raw)

    def __init__(self, serial, sm, wake_detector, transcribir_fn,
                 detener_event, muted_event, audio_io=None):
        self._serial = serial
        self._sm = sm
        self._wake = wake_detector
        self._transcribir = transcribir_fn
        self._detener = detener_event
        self._muted = muted_event
        self._audio_io = audio_io
        self._dev_logged = False         # ya logueamos info del device
        self._t_last_err = 0.0           # throttle errores de audio
        self._device: Optional[int] = None  # indice del device en uso

    def _log_error(self, msg: str) -> None:
        ahora = time.monotonic()
        if ahora - self._t_last_err < 5.0:
            return
        self._t_last_err = ahora
        console.print(f'[red][wake] {msg}[/]')

    def _seleccionar_device(self) -> Optional[int]:
        try:
            devices = sd.query_devices()
            default = sd.default.device[0]
            if default is not None and default >= 0 and default < len(devices):
                d = devices[default]
                self._device = default
                if not self._dev_logged:
                    console.print(f'[dim][wake] mic: "{d["name"]}" (device {default})[/]')
                    self._dev_logged = True
                return default
            # default no disponible -> buscar primer input
            for i, d in enumerate(devices):
                if d['max_input_channels'] > 0:
                    self._device = i
                    console.print(f'[yellow][wake] default muerto, fallback a "{d["name"]}" (device {i})[/]')
                    self._dev_logged = True
                    return i
            self._log_error('no hay dispositivos de entrada')
            return None
        except Exception as e:
            self._log_error(f'error listando devices: {e}')
            return None

    def _grabar_laptop(self) -> Optional[bytes]:
        device = self._seleccionar_device()
        if device is None:
            return None

        q: queue.Queue = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(bytes(indata))

        frame_bytes = int(_LAP_SAMPLE_RATE * FRAME_MS / 1000) * 2
        collected: list[bytes] = []
        t0 = time.monotonic()
        t_ultima_voz = 0.0

        try:
            with sd.RawInputStream(samplerate=_LAP_SAMPLE_RATE, channels=1,
                                   dtype='int16', blocksize=frame_bytes // 2,
                                   device=device, callback=callback):
                while time.monotonic() - t0 < WAKE_WINDOW_S and not self._detener.is_set():
                    if self._sm.en_conversacion:
                        return None
                    try:
                        chunk = q.get(timeout=0.1)
                        if len(chunk) >= frame_bytes:
                            collected.append(chunk[:frame_bytes])
                            arr = np.frombuffer(chunk[:frame_bytes], dtype=np.int16)
                            nivel = float(np.sqrt(np.mean(
                                arr.astype(np.float32) ** 2))) / 32767.0 * 100.0
                            ahora = time.monotonic()
                            if nivel >= WAKE_VOICE_LEVEL:
                                t_ultima_voz = ahora
                            elif (t_ultima_voz and
                                  ahora - t_ultima_voz >= WAKE_SILENCE_CUT_S):
                                break
                    except queue.Empty:
                        pass
        except sd.PortAudioError as e:
            self._log_error(f'PortAudio error en device {device}: {e}')
            self._dev_logged = False
            return None
        except Exception as e:
            self._log_error(f'error grabando wake: {e}')
            self._dev_logged = False
            return None

        if not collected:
            return None

        raw = b''.join(collected)
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
