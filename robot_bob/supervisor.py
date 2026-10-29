"""
supervisor.py — Lanza a Bob (main.py) y lo vuelve a lanzar cuando hace falta (Fase S, S5).

  python supervisor.py [--alone]      (los argumentos pasan tal cual a main.py)

Según cómo sale main.py:
  0          Bob se cerró a propósito (Q, Ctrl+C, el CLI): el supervisor también.
  REINICIAR  Bob pidió reiniciarse (p. ej. tras arreglar su código, S6): se relanza ya.
             Más de MAX_REINICIOS pedidos en VENTANA_S es un bucle: para.
  otro       Se cayó: se relanza tras ESPERA_CAIDA_S. Si se cayó MAX_CAIDAS veces en
             VENTANA_S, para y sale con el último código.

Arreglo recién aplicado (S6): Bob deja logs/arreglos/pendiente.json antes de pedir el
reinicio. Si la corrida siguiente se cae antes de ESTABLE_S, el supervisor pone de vuelta
las copias de los archivos que editó (revertir_arreglo) y lo relanza como en una caída.
Si dura más o se cierra a propósito, el arreglo queda y se borra la marca.

main.py hereda stdin, stdout y stderr: con el CLI, los comandos y los eventos pasan como
si no hubiera supervisor. main.py recibe BOB_SUPERVISADO=1 para saber que puede pedir un
reinicio. Cada lanzamiento vuelve a abrir COM3, y eso resetea el DevKit.

No importa nada de Bob: un error en su código no tumba al supervisor.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from typing import Callable, List, Optional

REINICIAR = 3
MAX_CAIDAS = 3
MAX_REINICIOS = 5
VENTANA_S = 300.0
ESPERA_CAIDA_S = 2.0
ESTABLE_S = 90.0

AQUI = os.path.dirname(os.path.abspath(__file__))
PENDIENTE = os.path.join(AQUI, 'logs', 'arreglos', 'pendiente.json')


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


def revertir_arreglo(pendiente: str) -> int:
    """Pone de vuelta los archivos del arreglo marcado en `pendiente` y borra la marca.
    Devuelve cuántos archivos tocó."""
    with open(pendiente, encoding='utf-8') as f:
        marca = json.load(f)
    with open(marca['copias'], encoding='utf-8') as f:
        copias = json.load(f)                # ruta → copia del original, o None si era nuevo
    for destino, copia in copias.items():
        if copia is None:
            if os.path.exists(destino):
                os.remove(destino)
        else:
            shutil.copyfile(copia, destino)
    os.remove(pendiente)
    return len(copias)


def _revisar_arreglo(pendiente: Optional[str], codigo: int, duracion: float,
                     avisar: Callable[[str], None]) -> None:
    """Tras cada salida de main.py: decide si el arreglo recién aplicado se queda."""
    if not pendiente or codigo == REINICIAR or not os.path.exists(pendiente):
        return
    try:
        if codigo == 0 or duracion >= ESTABLE_S:
            os.remove(pendiente)             # arrancó bien con el arreglo: se queda
            return
        n = revertir_arreglo(pendiente)
        avisar(f'Bob se cayó a los {duracion:.0f} s con su arreglo nuevo: deshice el arreglo '
               f'({n} archivo(s)).')
    except Exception as e:                   # el supervisor no se cae por esto
        avisar(f'No pude revisar el arreglo pendiente ({e}). Revisa {pendiente}.')


def supervisar(args: List[str], lanzar: Callable[[List[str]], int] = lanzar_main,
               reloj: Callable[[], float] = time.monotonic,
               dormir: Callable[[float], None] = time.sleep,
               avisar: Callable[[str], None] = _avisar,
               pendiente: Optional[str] = PENDIENTE) -> int:
    caidas: List[float] = []
    reinicios: List[float] = []
    while True:
        inicio = reloj()
        codigo = lanzar(args)
        ahora = reloj()
        _revisar_arreglo(pendiente, codigo, ahora - inicio, avisar)
        if codigo == 0:
            return 0
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
