"""
test_modos.py — Modos expresivos (teclas 1 a 4) sin hardware ni red.

Corre cada modo con un serial y un pipeline falsos (sin esperas reales) y revisa:
caras y emociones válidas, gestos que existen, servos dentro de los límites y en
pasitos suaves, duración de 10 a 20 s, cabeza de vuelta en casa, el corte con D/J
y que suelte OLED y BehaviorEngine.

  python tests/unit/test_modos.py
"""
import os
import sys
import threading
import time as _time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from nucleo import modos, show                  # noqa: E402
from voz.pipeline.emocion import _VALID_EMOS    # noqa: E402

fallos = 0
CASA = (90.0, 55.0)


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class Serial:
    def __init__(self):
        self.estados, self.servos = [], []

    def cmd_estado(self, e):
        self.estados.append(e)

    def cmd_servo(self, pan, tilt):
        self.servos.append((pan, tilt))


class SM:
    def __init__(self):
        self.show_activo = threading.Event()
        self.oled = 0

    def oled_ocupar(self):
        self.oled += 1

    def oled_liberar(self):
        self.oled -= 1


class VP:
    def __init__(self):
        self._sm, self._serial = SM(), Serial()
        self.dichas = []
        self._hablar = lambda texto, emo=None: self.dichas.append((texto, emo))


class _SinEspera:                     # time del módulo show, sin dormir
    sleep = staticmethod(lambda s: None)
    monotonic = staticmethod(_time.monotonic)


def main() -> None:
    show.time = _SinEspera
    modos._home = lambda: CASA
    try:
        check('modos: los 4 del plan, en orden de tecla',
              list(modos.MODOS) == ['coqueto', 'miedoso', 'gruñon', 'dramatico'],
              f'-> {list(modos.MODOS)}')
        for nombre, pasos in modos.MODOS.items():
            gestos = [g for _, _, g in pasos if g]
            check(f'{nombre}: gestos existen en _Cabeza',
                  all(hasattr(show._Cabeza, g) for g in gestos), f'-> {gestos}')
            vp = VP()
            check(f'{nombre}: corre', modos.run_modo(vp, nombre) is True)
            s = vp._serial
            check(f'{nombre}: dice todos los pasos', len(vp.dichas) == len(pasos),
                  f'-> {len(vp.dichas)}/{len(pasos)}')
            check(f'{nombre}: caras válidas', set(s.estados) <= _VALID_EMOS,
                  f'-> {set(s.estados) - _VALID_EMOS}')
            check(f'{nombre}: emociones de voz válidas',
                  all(e in _VALID_EMOS for _, e in vp.dichas))
            check(f'{nombre}: servos dentro de los límites',
                  all(20 <= p <= 160 and 40 <= t <= 130 for p, t in s.servos))
            pasos_srv = [max(abs(a[0] - b[0]), abs(a[1] - b[1]))
                         for a, b in zip(s.servos, s.servos[1:])]
            check(f'{nombre}: pasitos suaves (≤ 12° entre comandos)',
                  max(pasos_srv) <= 12, f'-> {max(pasos_srv)}')
            check(f'{nombre}: termina con la cabeza en casa', s.servos[-1] == CASA,
                  f'-> {s.servos[-1]}')
            palabras = sum(len(t.split()) for t, _ in vp.dichas)
            check(f'{nombre}: duración de 10 a 20 s de voz', 10 <= palabras / 2.6 <= 20,
                  f'-> ~{palabras / 2.6:.0f} s ({palabras} palabras)')
            check(f'{nombre}: libera OLED y BehaviorEngine',
                  vp._sm.oled == 0 and not vp._sm.show_activo.is_set())

        # Teclas D/J: se corta en el próximo paso y suelta todo
        vp = VP()
        vp._cortado = lambda: len(vp.dichas) >= 1
        modos.run_modo(vp, 'miedoso')
        check('corte: deja de hablar ahí mismo', len(vp.dichas) == 1,
              f'-> {len(vp.dichas)} frases')
        check('corte: vuelve a casa y libera todo', vp._serial.servos[-1] == CASA
              and vp._sm.oled == 0 and not vp._sm.show_activo.is_set())

        vp = VP()
        check('modo desconocido: False y no toca nada',
              modos.run_modo(vp, 'bailarin') is False and not vp._serial.servos
              and not vp._sm.show_activo.is_set())
    finally:
        show.time = _time

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
