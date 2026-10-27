"""
test_presentaciones_cli.py — El show (P), la foto (T) y los modos (1 a 4) muestran en el
CLI lo que dice Bob, sin hardware ni red.

Corre cada presentación con un serial y un pipeline falsos, captura lo que se imprime
(consola de Rich y la marca de turno de puente_cli) y revisa que cada frase dicha salga
una vez, en orden, como turno de Bob.

  python tests/unit/test_presentaciones_cli.py
"""
import contextlib
import os
import sys
import threading
import time as _time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from nucleo import foto, modos, puente_cli, show   # noqa: E402
from voz.pipeline import consola                    # noqa: E402

fallos = 0
mostradas = []          # (turno, línea) de cada console.print
_turno = [None]


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


@contextlib.contextmanager
def turno_falso(quien):
    _turno[0] = quien
    try:
        yield
    finally:
        _turno[0] = None


class Consola:
    print = staticmethod(lambda linea: mostradas.append((_turno[0], linea)))


class Serial:
    def cmd_estado(self, e): pass
    def cmd_servo(self, pan, tilt): pass
    def cmd_motor(self, izq, der): pass


class SM:
    def __init__(self):
        self.show_activo, self.bailando = threading.Event(), threading.Event()
        self.musica_sonando = threading.Event()

    def oled_ocupar(self): pass
    def oled_liberar(self): pass


class VP:
    def __init__(self):
        self._sm, self._serial = SM(), Serial()
        self.dichas = []
        self._hablar = lambda texto, emo=None: self.dichas.append((texto, emo))
        self._face_id = self._memoria = self._get_frame = None


class _SinEspera:                     # time de show/foto, sin dormir
    sleep = staticmethod(lambda s: None)
    monotonic = staticmethod(_time.monotonic)


def revisar(nombre: str, correr) -> None:
    mostradas.clear()
    vp = VP()
    correr(vp)
    check(f'{nombre}: dice algo', bool(vp.dichas))
    check(f'{nombre}: cada frase se muestra una vez, como turno de Bob',
          len(mostradas) == len(vp.dichas) and all(q == 'bob' for q, _ in mostradas),
          f'-> {len(mostradas)} mostradas / {len(vp.dichas)} dichas')
    check(f'{nombre}: en orden, con su texto y su emoción',
          all(t in linea and f'({e})' in linea
              for (t, e), (_, linea) in zip(vp.dichas, mostradas)))


def main() -> None:
    originales = (puente_cli.turno, consola.console, show.time, foto.time,
                  show.MUSICA_ENABLED, show.obtener_clima, show.hora_actual)
    puente_cli.turno, consola.console = turno_falso, Consola
    show.time = foto.time = _SinEspera
    show.MUSICA_ENABLED = False
    show.obtener_clima = lambda: 'soleado, 12 grados'
    show.hora_actual = lambda: 'diez y media'
    foto.FOTO_SEGUNDOS = 0
    foto._home = modos._home = lambda: (90.0, 55.0)
    try:
        revisar('show (P)', show.run_show)
        revisar('foto (T)', lambda vp: foto.run_foto(vp, 'MUY_FELIZ'))
        for tecla, nombre in modos.TECLAS.items():
            revisar(f'modo {nombre} ({tecla})', lambda vp, n=nombre: modos.run_modo(vp, n))
    finally:
        (puente_cli.turno, consola.console, show.time, foto.time,
         show.MUSICA_ENABLED, show.obtener_clima, show.hora_actual) = originales

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
