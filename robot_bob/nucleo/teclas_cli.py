"""
teclas_cli.py — Atajos del CLI (bob-cli) hacia el loop principal de main.py.

El CLI manda {"cmd": "tecla", "tecla": "s"} (y "nombre" con la E). La tecla entra a una
cola que el loop lee cuando cv2.waitKey no trae ninguna, así que S, D, J, P, T, 1 a 4 y E
hacen lo mismo en la ventana de video y en el CLI. ENTER llega como "turno" (modo tecla).
Q (o "salir") no va por la cola: marca `salida`, que el loop mira al empezar cada
vuelta, también cuando no llega video (ahí no se leen teclas).

registrar() va antes de puente_cli.activar(): un comando que llega sin registrar se
pierde (ver puente_cli).
"""
from __future__ import annotations

import queue
import signal
import threading
from typing import Optional, Tuple

from nucleo import puente_cli

TURNO = -1               # código de "turno" (ENTER del CLI); no choca con ninguna tecla
SIN_TECLA = 255          # lo que da cv2.waitKey(1) & 0xFF cuando no hay tecla
_LETRAS = ('s', 'd', 'j', 'p', 't', 'e', '1', '2', '3', '4')   # 1 a 4: modos.TECLAS

en_loop = threading.Event()  # main lo marca al entrar al loop principal
salida = threading.Event()   # Q del CLI: el loop termina en su próxima vuelta
_cola: "queue.Queue[Tuple[int, Optional[str]]]" = queue.Queue()


def registrar() -> None:
    puente_cli.al_comando('tecla', _tecla)
    puente_cli.al_comando('salir', _salir)


def siguiente() -> Tuple[int, Optional[str]]:
    """La próxima tecla del CLI como (código, nombre). (SIN_TECLA, None) si no hay."""
    try:
        return _cola.get_nowait()
    except queue.Empty:
        return SIN_TECLA, None


def _tecla(comando: dict) -> None:
    tecla = str(comando.get('tecla', '')).lower()
    if tecla == 'q':
        _salir(comando)
    elif tecla == 'turno':
        _cola.put((TURNO, None))
    elif tecla in _LETRAS:
        nombre = comando.get('nombre')
        _cola.put((ord(tecla), str(nombre).strip() if nombre else None))
    else:
        raise ValueError(f'tecla desconocida: {tecla!r}')   # puente_cli lo avisa


def _salir(comando: dict) -> None:
    """Q del CLI, o el CLI se fue (stdin cerrado). En el loop, como la Q de la ventana
    de video; antes (esperando la cámara, cargando modelos), como Ctrl+C."""
    if en_loop.is_set():
        salida.set()
    else:
        signal.raise_signal(signal.SIGINT)
