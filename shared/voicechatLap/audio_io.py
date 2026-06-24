"""
audio_io.py — Capa de transporte con el ESP32.

Protocolo:
  PORT_MIC (ESP32 -> laptop): stream continuo de uint8 @ 8 kHz, sin framing.
  PORT_SPK (laptop -> ESP32): mensajes con framing
        | 4 bytes BE length | N bytes uint8 |
     Códigos especiales de length:
        0xFFFFFFFF  = STOP   (corta playback ahora, sin payload)
        0xFFFFFFFE  = KEEPALIVE (sin payload)

La clase AudioIO conecta los dos sockets y arranca un hilo lector que
empuja el audio del mic a una cola. El productor lo consume frame a frame.
"""

from __future__ import annotations

import socket
import struct
import threading
import queue
import time
from typing import Optional

from config import (
    ESP32_IP, PORT_MIC, PORT_SPK,
    MIC_CHUNK_BYTES, STOP_CODE, KEEPALIVE_CODE,
)


class AudioIO:
    def __init__(
        self,
        ip: str = ESP32_IP,
        port_mic: int = PORT_MIC,
        port_spk: int = PORT_SPK,
        rx_chunk: int = MIC_CHUNK_BYTES,
    ):
        self.ip = ip
        self.port_mic = port_mic
        self.port_spk = port_spk
        self.rx_chunk = rx_chunk

        self.sock_mic: Optional[socket.socket] = None
        self.sock_spk: Optional[socket.socket] = None

        self.mic_q: "queue.Queue[bytes]" = queue.Queue(maxsize=512)
        self._rx_thread: Optional[threading.Thread] = None
        self._rx_stop = threading.Event()
        self._spk_lock = threading.Lock()       # serializa sendall
        self.connected = False

    # ---------- ciclo de vida ----------

    def connect(self, timeout_s: float = 10.0) -> None:
        """Abre los dos sockets contra el ESP32 y arranca el hilo lector."""
        self.sock_mic = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock_mic.settimeout(timeout_s)
        self.sock_mic.connect((self.ip, self.port_mic))
        self.sock_mic.settimeout(None)          # bloquea en recv

        self.sock_spk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock_spk.settimeout(timeout_s)
        self.sock_spk.connect((self.ip, self.port_spk))
        self.sock_spk.settimeout(None)

        self._rx_stop.clear()
        self.connected = True
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True, name="esp32-mic-rx")
        self._rx_thread.start()

    def close(self) -> None:
        """Cierra sockets y para el hilo. Idempotente."""
        self._rx_stop.set()
        self.connected = False
        for s in (self.sock_mic, self.sock_spk):
            if s is not None:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    s.close()
                except OSError:
                    pass
        self.sock_mic = self.sock_spk = None
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=1.0)
            self._rx_thread = None

    # ---------- hilo lector del mic ----------

    def _rx_loop(self) -> None:
        sock = self.sock_mic
        if sock is None:
            return
        try:
            while not self._rx_stop.is_set():
                data = sock.recv(self.rx_chunk)
                if not data:
                    break
                try:
                    self.mic_q.put_nowait(data)
                except queue.Full:
                    # cola saturada: descartamos el más viejo para mantener
                    # latencia real-time
                    try:
                        self.mic_q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self.mic_q.put_nowait(data)
                    except queue.Full:
                        pass
        except OSError:
            pass
        finally:
            self.connected = False

    # ---------- consumo del mic ----------

    def get_mic(self, timeout: float = 1.0) -> Optional[bytes]:
        """Devuelve el próximo chunk uint8 del ESP32, o None si timeout."""
        try:
            return self.mic_q.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain_mic(self) -> None:
        """Vacía la cola — útil después de un periodo de inactividad o tras
        un cambio de modo para no procesar audio viejo."""
        try:
            while True:
                self.mic_q.get_nowait()
        except queue.Empty:
            pass

    # ---------- envío al speaker del ESP32 ----------

    def send_audio_chunk(self, audio: bytes) -> bool:
        """Manda un mensaje completo (header + body) al ESP32. Útil para
        respuestas cortas que caben en una sola llamada."""
        if self.sock_spk is None or not self.connected:
            return False
        try:
            header = struct.pack(">I", len(audio))
            with self._spk_lock:
                self.sock_spk.sendall(header + audio)
            return True
        except OSError:
            self.connected = False
            return False

    def send_audio_header(self, total_len: int) -> bool:
        """Abre un mensaje grande mandando solo el length. Después debes
        mandar exactamente total_len bytes con send_audio_body()."""
        if self.sock_spk is None or not self.connected:
            return False
        try:
            with self._spk_lock:
                self.sock_spk.sendall(struct.pack(">I", total_len))
            return True
        except OSError:
            self.connected = False
            return False

    def send_audio_body(self, chunk: bytes) -> bool:
        """Manda un trozo de un mensaje ya abierto con send_audio_header.
        El ESP32 lo concatena con lo anterior dentro del mismo playback,
        sin volver a entrar en modo LISTEN entre chunks."""
        if self.sock_spk is None or not self.connected or not chunk:
            return False
        try:
            with self._spk_lock:
                self.sock_spk.sendall(chunk)
            return True
        except OSError:
            self.connected = False
            return False

    def send_stop(self) -> bool:
        """Manda 0xFFFFFFFF para que el ESP32 corte su playback."""
        if self.sock_spk is None or not self.connected:
            return False
        try:
            with self._spk_lock:
                self.sock_spk.sendall(struct.pack(">I", STOP_CODE))
            return True
        except OSError:
            self.connected = False
            return False

    def send_keepalive(self) -> bool:
        if self.sock_spk is None or not self.connected:
            return False
        try:
            with self._spk_lock:
                self.sock_spk.sendall(struct.pack(">I", KEEPALIVE_CODE))
            return True
        except OSError:
            self.connected = False
            return False
