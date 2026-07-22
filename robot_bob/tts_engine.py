"""
tts_engine.py — Síntesis y reproducción de voz (laptop y ESP32).
"""
from __future__ import annotations

import subprocess
import time
from typing import Optional

import numpy as np
import sounddevice as sd
import imageio_ffmpeg

from config import (
    TTS_FFMPEG_FILTERS, SAMPLE_RATE, TTS_TAIL_S,
    USE_ROBOT_SPEAKER,
)
from audio_helpers import synthesize_mp3

FFMPEG_TTS = imageio_ffmpeg.get_ffmpeg_exe()


class TTSEngine:
    def __init__(self, audio_io, detener_event, discovery_module=None):
        self._audio_io = audio_io
        self._detener = detener_event
        self._discovery = discovery_module
        self._t_audio_retry = 0.0

    def _hablar(self, texto, emo=None):
        mp3 = synthesize_mp3(texto, emo)
        if not mp3:
            return
        self._reproducir_mp3(mp3)

    def _reproducir_mp3(self, mp3):
        if USE_ROBOT_SPEAKER and self._audio_io is not None:
            if not self._audio_io.connected:
                self._audio_reconectar()
            if self._audio_io.connected:
                return self._reproducir_mp3_robot(mp3)
        return self._reproducir_mp3_laptop(mp3)

    def _audio_reconectar(self):
        ahora = time.monotonic()
        if ahora - self._t_audio_retry < 10.0:
            return
        self._t_audio_retry = ahora
        try:
            if self._discovery:
                ip = self._discovery.cache_ips().get('ESP32_IP')
                if ip:
                    self._audio_io.ip = ip
        except Exception:
            pass
        try:
            self._audio_io.close()
            self._audio_io.connect(timeout_s=3.0)
            from rich.console import Console
            Console().print(f'[green][audio] ✓ reconectado al ESP32 ({self._audio_io.ip})[/]')
        except Exception as e:
            from rich.console import Console
            Console().print(f'[dim][audio] ESP32 no responde ({e}); hablo por la laptop[/]')

    def _reproducir_mp3_robot(self, mp3):
        u8 = self._mp3_a_u8(mp3)
        if not u8:
            return None
        total = len(u8)
        if not self._audio_io.send_audio_header(total):
            return None
        t0 = time.monotonic()
        if not self._audio_io.send_audio_body(u8):
            return None
        restante = total / SAMPLE_RATE - (time.monotonic() - t0)
        if restante > 0:
            self._detener.wait(restante)
        time.sleep(TTS_TAIL_S)
        return None

    @staticmethod
    def _mp3_a_u8(mp3):
        proc = subprocess.run(
            [FFMPEG_TTS, '-hide_banner', '-loglevel', 'error',
             '-i', 'pipe:0',
             '-af', TTS_FFMPEG_FILTERS,
             '-ar', str(SAMPLE_RATE), '-ac', '1',
             '-dither_method', 'triangular',
             '-acodec', 'pcm_u8', '-f', 'u8', 'pipe:1'],
            input=mp3, capture_output=True, check=False,
        )
        return proc.stdout

    @staticmethod
    def _mp3_a_pcm_f32(mp3, sample_rate=24000):
        proc = subprocess.run(
            [FFMPEG_TTS, '-hide_banner', '-loglevel', 'error',
             '-i', 'pipe:0',
             '-ar', str(sample_rate), '-ac', '1',
             '-f', 'f32le', 'pipe:1'],
            input=mp3, capture_output=True, check=False,
        )
        if not proc.stdout:
            return None
        return np.frombuffer(proc.stdout, dtype=np.float32)

    def _reproducir_mp3_laptop(self, mp3):
        pcm = self._mp3_a_pcm_f32(mp3)
        if pcm is None or len(pcm) == 0:
            return None
        sd.play(pcm, samplerate=24000)
        while sd.get_stream() and sd.get_stream().active:
            time.sleep(0.05)
            if self._detener.is_set():
                sd.stop()
                break
        return None
