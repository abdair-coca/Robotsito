"""
robot_serial.py — Cliente serial opcional para mandar comandos ESTADO al ESP32.

El ESP32 (Esp32/main.py) acepta tres tipos de comandos por su USB serial:
    H:90,V:45             -> servos pan/tilt
    ESTADO:FELIZ          -> sobrescribir el estado emocional del OLED
    SIGUIENDO:0.12,-0.34  -> coords normalizadas del rostro a seguir

Este módulo solo manda ESTADO desde el VoiceChat. Si el COM port no está
disponible (porque seguimiento_facial.py lo tiene abierto, porque el ESP32
no está enchufado por USB, o porque el puerto está mal configurado), el
cliente queda en modo no-op silencioso — el voice chat sigue funcionando
sin OLED.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

try:
    import serial   # pyserial
    _PYSERIAL_OK = True
except ImportError:
    _PYSERIAL_OK = False


# Palabras que disparan la cara FELIZ en lugar de HABLANDO normal.
# Se usan como contains() sobre el texto del LLM en lowercase.
HAPPY_KEYWORDS = (
    "genial", "perfecto", "claro", "supuesto", "feliz", "increíble",
    "excelente", "fantástico", "maravilloso", "súper", "encantado",
    "great", "sure", "happy", "wonderful", "love", "amazing", "awesome",
)


class RobotSerial:
    """Wrapper alrededor de pyserial.Serial. Reentrante seguro vía un lock.
    Hace debouncing por estado: no manda el mismo estado dos veces seguidas
    salvo que haya pasado más de min_repeat_s."""

    def __init__(
        self,
        port: Optional[str],
        baud: int = 115200,
        min_repeat_s: float = 1.0,
    ) -> None:
        self.port = port
        self.baud = baud
        self._ser: Optional["serial.Serial"] = None
        self._lock = threading.Lock()
        self._last_state = ""
        self._last_send = 0.0
        self._min_repeat = min_repeat_s

        if port and _PYSERIAL_OK:
            self._connect()

    def _connect(self) -> None:
        try:
            s = serial.Serial()
            s.port = self.port
            s.baudrate = self.baud
            s.timeout = 1
            # No resetear el ESP32 al abrir
            s.dtr = False
            s.rts = False
            s.open()
            # pequeño settle tras abrir el puerto
            time.sleep(0.3)
            self._ser = s
        except Exception:
            # COM port ocupado o ESP32 no conectado — no es error fatal
            self._ser = None

    @property
    def connected(self) -> bool:
        return self._ser is not None

    def estado(self, estado: str) -> None:
        """Manda ESTADO:XX. Silencia repeticiones rápidas del mismo estado."""
        if self._ser is None or not estado:
            return
        now = time.time()
        if estado == self._last_state and (now - self._last_send) < self._min_repeat:
            return
        self._last_state = estado
        self._last_send = now
        with self._lock:
            try:
                self._ser.write(f"ESTADO:{estado}\n".encode())
            except Exception:
                pass

    def close(self) -> None:
        if self._ser is not None:
            with self._lock:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None


def is_happy(text: str) -> bool:
    """Heurística simple: ¿el texto del LLM contiene palabras 'felices'?"""
    if not text:
        return False
    low = text.lower()
    return any(k in low for k in HAPPY_KEYWORDS)
