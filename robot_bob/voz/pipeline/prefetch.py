"""Prefetch del LLM mientras JEV decide la ruta."""
from __future__ import annotations

import queue
import threading
from typing import Optional, Dict


class _Prefetch:
    """R1: pedido al LLM lanzado antes de saber la ruta (JEV corre en paralelo).
    Lee el stream en un hilo y guarda los chunks; se consume como el stream original
    o se cancela si JEV dice que el turno era un comando."""

    def __init__(self, llm, kwargs: Dict):
        self.llm = llm
        self.kwargs = kwargs
        self.error: Optional[Exception] = None
        self._q: queue.Queue = queue.Queue()
        self._arranco = threading.Event()     # primer chunk o fin
        self._cancelar = threading.Event()
        self._stream = None
        threading.Thread(target=self._leer, daemon=True, name='llm-prefetch').start()

    def _leer(self) -> None:
        try:
            self._stream = self.llm.chat.completions.create(**self.kwargs)
            for chunk in self._stream:
                if self._cancelar.is_set():
                    break
                self._q.put(chunk)
                self._arranco.set()
        except Exception as e:
            self.error = e
        finally:
            self._q.put(None)
            self._arranco.set()
            if self._cancelar.is_set():
                self._cerrar_stream()

    def _cerrar_stream(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
        except Exception:
            pass

    def esperar_inicio(self, timeout: float) -> bool:
        """True si llegó al menos un chunk (o terminó sin error) antes del plazo."""
        self._arranco.wait(timeout)
        return self._arranco.is_set() and not (self.error and self._q.qsize() <= 1)

    def __iter__(self):
        while True:
            chunk = self._q.get()
            if chunk is None:
                # E-C: un error a mitad del stream se relanza; antes terminaba en silencio.
                if self.error is not None:
                    raise self.error
                return
            yield chunk

    def close(self) -> None:
        self._cancelar.set()
        self._cerrar_stream()
