"""
supervisor.py — Lanza a Bob (main.py) y lo vuelve a lanzar cuando hace falta (Fase S, S5).

  python supervisor.py [--alone]      (los argumentos pasan tal cual a main.py)

Según cómo sale main.py:
  0          Bob se cerró a propósito (Q, Ctrl+C, el CLI): el supervisor también.
  REINICIAR  Bob pidió reiniciarse (p. ej. tras arreglar su código, S6): se relanza ya.
             Más de MAX_REINICIOS pedidos en VENTANA_S es un bucle: para.
  otro       Se cayó: se relanza tras ESPERA_CAIDA_S. Si se cayó MAX_CAIDAS veces en
             VENTANA_S, para y sale con el último código.

main.py hereda stdin, stdout y stderr: con el CLI, los comandos y los eventos pasan como
si no hubiera supervisor. main.py recibe BOB_SUPERVISADO=1 para saber que puede pedir un
reinicio. Cada lanzamiento vuelve a abrir COM3, y eso resetea el DevKit.

No importa nada de Bob: un error en su código no tumba al supervisor.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Callable, List

REINICIAR = 3
MAX_CAIDAS = 3
MAX_REINICIOS = 5
VENTANA_S = 300.0
ESPERA_CAIDA_S = 2.0

AQUI = os.path.dirname(os.path.abspath(__file__))


def lanzar_main(args: List[str]) -> int:
    """Corre main.py hasta que termina y devuelve su código."""
    env = {**os.environ, 'BOB_SUPERVISADO': '1'}
    proc = subprocess.Popen([sys.executable, os.path.join(AQUI, 'main.py'), *args],
                            cwd=AQUI, env=env)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        # El Ctrl+C también le llega a main.py, que cierra limpio: se espera y no se relanza.
        proc.wait()
        return 0


def _avisar(texto: str) -> None:
    print(f'[supervisor] {texto}', flush=True)


def supervisar(args: List[str], lanzar: Callable[[List[str]], int] = lanzar_main,
               reloj: Callable[[], float] = time.monotonic,
               dormir: Callable[[float], None] = time.sleep,
               avisar: Callable[[str], None] = _avisar) -> int:
    caidas: List[float] = []
    reinicios: List[float] = []
    while True:
        codigo = lanzar(args)
        if codigo == 0:
            return 0
        ahora = reloj()
        if codigo == REINICIAR:
            reinicios = [t for t in reinicios if ahora - t < VENTANA_S] + [ahora]
            if len(reinicios) > MAX_REINICIOS:
                avisar(f'Bob pidió reiniciarse {len(reinicios)} veces en '
                       f'{VENTANA_S / 60:.0f} min: parece un bucle, no lo relanzo más.')
                return codigo
            avisar('Bob pidió reiniciarse: lo vuelvo a lanzar.')
            continue
        caidas = [t for t in caidas if ahora - t < VENTANA_S] + [ahora]
        if len(caidas) >= MAX_CAIDAS:
            avisar(f'Bob se cayó {len(caidas)} veces en {VENTANA_S / 60:.0f} min '
                   f'(código {codigo}): no lo relanzo más.')
            return codigo
        avisar(f'Bob se cayó (código {codigo}): lo relanzo en {ESPERA_CAIDA_S:.0f} s.')
        dormir(ESPERA_CAIDA_S)


if __name__ == '__main__':
    sys.exit(supervisar(sys.argv[1:]))
