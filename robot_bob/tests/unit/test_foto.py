"""
test_foto.py — Modo foto ("Bob, ponte para la foto") sin hardware ni red.

Revisa qué frases piden una foto y qué cara, y corre la pose con un serial y un
pipeline falsos (sin esperas reales): cara válida en el OLED, servos dentro de los
límites, cabeza que vuelve a su lugar y OLED/BehaviorEngine liberados al final.

  python tests/unit/test_foto.py
"""
import os
import sys
import threading
import time as _time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from nucleo import foto, show                   # noqa: E402
from voz.pipeline.emocion import _VALID_EMOS    # noqa: E402

fallos = 0


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
    def __init__(self, cortar=False):
        self._sm, self._serial = SM(), Serial()
        self.dichas = []
        self._hablar = lambda texto, emo=None: self.dichas.append((texto, emo))
        self._cortado = lambda: cortar


class _SinEspera:                     # time de foto/show, sin dormir; el reloj avanza solo
    t = 0.0

    @classmethod
    def sleep(cls, s):
        cls.t += s

    @classmethod
    def monotonic(cls):
        return cls.t


def main() -> None:
    # 1) Qué frases piden foto y qué cara
    casos = (
        ('Bob, ponte para la foto', 'MUY_FELIZ'),
        ('ponte feliz para la foto', 'FELIZ'),
        ('ponte muy triste para la foto', 'MUY_TRISTE'),
        ('ponte triste para la foto', 'TRISTE'),
        ('pon cara de enojado para la foto', 'ENOJADO'),
        ('haz una pose de sorprendido', 'SORPRENDIDO'),
        ('posa como enamorado', 'AMOR'),
        ('sonríe para la selfie', 'MUY_FELIZ'),
        ('tomemos una foto, ponte travieso', 'TRAVIESO'),
        ('sácame una foto con cara de asustada', 'ASUSTADO'),
    )
    for texto, esperada in casos:
        cara = foto.cara_foto(texto)
        check(f'pide foto: "{texto}" -> {esperada}', cara == esperada, f'-> {cara}')
    for texto in ('¿te gustan las fotos?', 'estoy feliz', 'ayer vi una foto tuya',
                  'la mariposa es bonita', 'preséntate'):
        check(f'no es foto: "{texto}"', foto.cara_foto(texto) is None)
    check('JEV ruta foto sin cara -> pose por defecto', foto.cara_foto('dale', 'foto') == 'MUY_FELIZ')
    check('JEV ruta charla + palabras clave -> foto', foto.cara_foto('ponte para la foto', 'charla')
          == 'MUY_FELIZ')
    check('JEV ruta música -> no es foto', foto.cara_foto('ponte para la foto', 'musica') is None)
    check('caras: todas existen en el OLED', {c for c, _ in foto.CARAS} <= _VALID_EMOS)
    check('caras: todas tienen pose y frase', {c for c, _ in foto.CARAS}
          == set(foto._POSES) == set(foto._CARA_DICHA))

    # 2) La pose, sin esperas reales
    foto.time = show.time = _SinEspera
    foto._home = lambda: (90.0, 55.0)
    try:
        for cara in sorted(foto._POSES):
            vp = VP()
            _SinEspera.t = 0.0
            foto.run_foto(vp, cara)
            s = vp._serial
            check(f'{cara}: OLED queda en la cara', s.estados[-1] == cara, f'-> {s.estados}')
            dentro = all(20 <= p <= 160 and 40 <= t <= 130 for p, t in s.servos)
            check(f'{cara}: servos dentro de los límites', dentro)
            check(f'{cara}: la cabeza vuelve al inicio', s.servos[-1] == (90.0, 55.0),
                  f'-> {s.servos[-1]}')
            check(f'{cara}: sostiene la pose', _SinEspera.t >= foto.FOTO_SEGUNDOS)
            check(f'{cara}: libera OLED y BehaviorEngine',
                  vp._sm.oled == 0 and not vp._sm.show_activo.is_set())
            check(f'{cara}: emociones de voz válidas', all(e in _VALID_EMOS for _, e in vp.dichas))

        vp = VP(cortar=True)
        _SinEspera.t = 0.0
        foto.run_foto(vp, 'FELIZ')
        check('tecla D/J corta la pose antes', _SinEspera.t < foto.FOTO_SEGUNDOS,
              f'-> {_SinEspera.t:.1f} s')
        vp = VP()
        foto.run_foto(vp, 'NO_EXISTE')
        check('cara desconocida -> pose por defecto', vp._serial.estados[-1] == 'MUY_FELIZ')
    finally:
        foto.time = show.time = _time

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
