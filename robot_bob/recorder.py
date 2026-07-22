"""
recorder.py — Grabación de audio desde micrófono (laptop o ESP32).
"""
from __future__ import annotations

import io as _io
import queue
import time
import wave
from collections import deque
from typing import Optional, List

import numpy as np
import sounddevice as sd
import webrtcvad

from config import (
    SAMPLE_RATE, FRAME_MS,
    USE_ROBOT_MIC,
    VAD_AGGRESSIVENESS, SILENCE_END_MS, MAX_RECORDING_S, MIN_SPEECH_S,
    PREROLL_FRAMES, NOISE_FLOOR_MIN,
)
from audio_helpers import rms_uint8, uint8_to_int16_bytes, uint8_to_wav

_LAP_SAMPLE_RATE = 16000
_SILENCE_FRAMES  = SILENCE_END_MS // FRAME_MS
_MAX_FRAMES      = (MAX_RECORDING_S * 1000) // FRAME_MS


class Recorder:
    def __init__(self, audio_io, detener_event, serial_mgr=None, state_machine=None):
        self._audio_io = audio_io
        self._detener = detener_event
        self._serial = serial_mgr
        self._sm = state_machine

    def grabar(self, initial_timeout=0):
        if USE_ROBOT_MIC and self._audio_io and self._audio_io.connected:
            return self._grabar_robot(initial_timeout)
        return self._grabar_laptop(initial_timeout)

    def _grabar_robot(self, initial_timeout):
        self._audio_io.drain_mic()
        vad_local   = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        u8_per_frame = SAMPLE_RATE * FRAME_MS // 1000

        preroll: deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        recorded: List[bytes] = []
        buffer = bytearray()
        speech_started = False
        silence_streak = 0
        speech_ms      = 0
        waited_ms      = 0
        initial_timeout_ms = int(initial_timeout * 1000)

        while True:
            chunk = self._audio_io.get_mic(timeout=0.5)
            if chunk is None:
                waited_ms += 500
                if not speech_started and waited_ms > initial_timeout_ms:
                    return None
                continue
            buffer.extend(chunk)

            while len(buffer) >= u8_per_frame:
                frame_u8 = bytes(buffer[:u8_per_frame])
                del buffer[:u8_per_frame]

                frame_i16 = uint8_to_int16_bytes(frame_u8)
                try:
                    is_vad = vad_local.is_speech(frame_i16, SAMPLE_RATE)
                except Exception:
                    is_vad = False

                rms = rms_uint8(frame_u8)

                if not speech_started:
                    preroll.append(frame_u8)
                    waited_ms += FRAME_MS
                    if is_vad and rms > NOISE_FLOOR_MIN:
                        speech_started = True
                        recorded.extend(preroll)
                        speech_ms = FRAME_MS * len(preroll)
                    elif waited_ms > initial_timeout_ms:
                        return None
                else:
                    recorded.append(frame_u8)
                    speech_ms += FRAME_MS
                    if is_vad and rms > NOISE_FLOOR_MIN:
                        silence_streak = 0
                    else:
                        silence_streak += 1
                    if silence_streak >= _SILENCE_FRAMES:
                        if speech_ms < MIN_SPEECH_S * 1000:
                            return None
                        raw = b''.join(recorded)
                        return uint8_to_wav(raw)
                    if speech_ms >= MAX_RECORDING_S * 1000:
                        raw = b''.join(recorded)
                        return uint8_to_wav(raw)

    def _grabar_laptop(self, initial_timeout=8.0):
        q: queue.Queue = queue.Queue()

        def callback(indata, frames, t, status):
            q.put(bytes(indata))

        frame_bytes = int(_LAP_SAMPLE_RATE * FRAME_MS / 1000) * 2
        vad_local   = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        initial_timeout_ms = int(initial_timeout * 1000)

        preroll:  deque[bytes] = deque(maxlen=PREROLL_FRAMES)
        recorded: List[bytes]  = []
        speech_started = False
        silence_streak = 0
        speech_ms      = 0
        waited_ms      = 0
        invite_sent    = False

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
                    if not speech_started and waited_ms > initial_timeout_ms:
                        return None
                    continue

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
                    if not invite_sent and waited_ms > 3000:
                        invite_sent = True
                        from expression_engine import pulse_emotion
                        pulse_emotion(self._serial, self._sm, 'CURIOSO', 700)
                    if is_vad and level > 1.5:
                        speech_started = True
                        recorded.extend(preroll)
                        speech_ms = FRAME_MS * len(preroll)
                    elif waited_ms > initial_timeout_ms:
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
