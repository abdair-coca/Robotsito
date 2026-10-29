"""
red.py — ¿Bob tiene internet? (Fase O, decisión O-D5).

Sin internet Bob no puede transcribir (el STT es Groq), pero las placas siguen en la
red local y las teclas, Piper y los soliloquios funcionan. Un hilo abre un socket a
api.groq.com:443 cada PERIODO_S; si no conecta en TIMEOUT_S, Bob queda sin red. Solo
abre el socket: no gasta cupo de Groq.

Un fallo de conexión de Groq llama a marcar_caida() y no espera al sondeo. El próximo
sondeo que conecte devuelve la red.

al_cambiar(en_linea) se llama una vez por cambio, desde el hilo del monitor o desde
quien llamó a marcar_caida().
"""
from __future__ import annotations

import socket
import threading
from typing import Callable, Optional

import errores

HOST = 'api.groq.com'
PUERTO = 443
PERIODO_S = 10.0
TIMEOUT_S = 2.0


def hay_internet(host: str = HOST, puerto: int = PUERTO, timeout: float = TIMEOUT_S) -> bool:
    """True si abre una conexión TCP con el host (incluye resolver el DNS)."""
    try:
        with socket.create_connection((host, puerto), timeout=timeout):
            return True
    except OSError:
        return False


class MonitorRed:
    """Arranca con red (optimista): el primer sondeo corre apenas arranca el hilo."""

    def __init__(self, al_cambiar: Optional[Callable[[bool], None]] = None,
                 sondear: Callable[[], bool] = hay_internet,
                 periodo_s: float = PERIODO_S) -> None:
        self._al_cambiar = al_cambiar
        self._sondear = sondear
        self._periodo_s = periodo_s
        self._en_linea = True
        self._lock = threading.Lock()
        self._detener = threading.Event()

    @property
    def en_linea(self) -> bool:
        return self._en_linea

    def iniciar(self) -> None:
        threading.Thread(target=self._loop, daemon=True, name='monitor-red').start()

    def cerrar(self) -> None:
        self._detener.set()

    def revisar(self) -> bool:
        """Sondea ahora. Devuelve si hay red."""
        self._poner(self._sondear())
        return self._en_linea

    def marcar_caida(self) -> None:
        self._poner(False)

    def _poner(self, en_linea: bool) -> None:
        with self._lock:
            if en_linea == self._en_linea:
                return
            self._en_linea = en_linea
        if self._al_cambiar is not None:
            try:
                self._al_cambiar(en_linea)
            except Exception as e:
                errores.aviso('RED', 'Falló el aviso de cambio de red.', e)

    def _loop(self) -> None:
        while not self._detener.is_set():
            self.revisar()
            self._detener.wait(self._periodo_s)
