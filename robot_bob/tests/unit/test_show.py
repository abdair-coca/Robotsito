"""
test_show.py — Modo presentación ("Bob, preséntate") sin hardware ni red.

Corre el show completo con un serial y un pipeline falsos (sin esperas reales) y revisa:
caras válidas, servos dentro de los límites, gestos que vuelven a su lugar, las
novedades nombradas en el guion, y el cierre con y sin música.

  python tests/unit/test_show.py
"""
import os
import sys
import threading
import time as _time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from nucleo import show                         # noqa: E402
from voz.pipeline.emocion import _VALID_EMOS    # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class Serial:
    def __init__(self):
        self.estados, self.servos, self.motores = [], [], []

    def cmd_estado(self, e):
        self.estados.append(e)

    def cmd_servo(self, pan, tilt):
        self.servos.append((pan, tilt))

    def cmd_motor(self, izq, der):
        self.motores.append((izq, der))


class SM:
    def __init__(self):
        self.show_activo, self.bailando = threading.Event(), threading.Event()
        self.musica_sonando = threading.Event()
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
        self._face_id = self._memoria = self._get_frame = None


class _SinEspera:                     # time del módulo show, sin dormir
    sleep = staticmethod(lambda s: None)
    monotonic = staticmethod(_time.monotonic)


def main() -> None:
    show.time = _SinEspera
    show.obtener_clima = lambda: 'soleado, 12 grados'
    show.hora_actual = lambda: 'diez y media'
    try:
        # 1) Sin música: corre entero y termina sin baile
        show.MUSICA_ENABLED = False
        vp = VP()
        t0 = _time.monotonic()
        con_baile = show.run_show(vp)
        check('show sin música: termina sin baile', con_baile is False)
        check('show: corre rápido sin esperas (sin trabarse)', _time.monotonic() - t0 < 20,
              f'-> {_time.monotonic() - t0:.1f} s')
        s = vp._serial
        validas = _VALID_EMOS | {'ESPERANDO', 'ESCUCHANDO', 'SIGUIENDO'}
        check('caras: todas existen', set(s.estados) <= validas, f'-> {set(s.estados) - validas}')
        check('caras: más expresivo (al menos 9 distintas)', len(set(s.estados)) >= 9,
              f'-> {sorted(set(s.estados))}')
        check('emociones de voz: todas válidas',
              all(e in _VALID_EMOS for _, e in vp.dichas if e), f'-> {[e for _, e in vp.dichas]}')
        dentro = all(20 <= p <= 160 and 40 <= t <= 130 for p, t in s.servos)
        check('servos: dentro de los límites', dentro)
        pasos = [max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(s.servos, s.servos[1:])]
        check('servos: pasitos suaves (≤ 12° entre comandos)', max(pasos) <= 12, f'-> {max(pasos)}')
        texto = ' '.join(t for t, _ in vp.dichas)
        for clave in ('modo ingeniero', 'navegador', 'voz de mi creador',
                      'de dónde lo saqué', 'recordame'):
            check(f'guion: nombra "{clave}"', clave in texto)
        check('show: libera OLED y BehaviorEngine', vp._sm.oled == 0 and not vp._sm.show_activo.is_set())
        palabras = len(texto.split())
        check('duración: entre 60 y 120 s de voz', 60 <= palabras / 2.6 <= 120,
              f'-> ~{palabras / 2.6:.0f} s ({palabras} palabras)')

        check('sin memoria de caras: no promete "te presento"', 'te presento' not in texto)

        # 1b) Con memoria de caras: reconoce y cuenta lo de "te presento a…"
        class Cara:
            listo = True
            analizar = staticmethod(lambda frame: ([0.1], 20))

        class Mem:
            reconocer = staticmethod(lambda emb: (1, 'Abdair'))
            total_personas = staticmethod(lambda: 17)
        vp = VP()
        vp._face_id, vp._memoria, vp._get_frame = Cara(), Mem(), (lambda: 'frame')
        show.run_show(vp)
        texto = ' '.join(t for t, _ in vp.dichas)
        check('con memoria: saluda por el nombre', '¡Abdair! A vos ya te conozco' in texto)
        check('con memoria: nombra "te presento"', 'te presento' in texto)
        palabras = len(texto.split())
        check('duración con todo: menos de 2 minutos', palabras / 2.6 <= 120,
              f'-> ~{palabras / 2.6:.0f} s ({palabras} palabras)')

        # 1c) Teclas D/J/P: el show se corta en la próxima frase y suelta todo
        vp = VP()
        vp._cortado = lambda: len(vp.dichas) >= 3
        con_baile = show.run_show(vp)
        check('corte: deja de hablar ahí mismo', len(vp.dichas) == 3, f'-> {len(vp.dichas)} frases')
        check('corte: sin baile y motores parados', con_baile is False
              and (not vp._serial.motores or vp._serial.motores[-1] == (0, 0)))
        check('corte: libera OLED y BehaviorEngine',
              vp._sm.oled == 0 and not vp._sm.show_activo.is_set())

        # 2) Con música: arranca el baile
        show.MUSICA_ENABLED = True
        from asistente import music
        original = music.ejecutar
        music.ejecutar = lambda intent: f'¡Dale! Poniendo {intent.query}.'
        try:
            vp = VP()
            check('show con música: termina bailando', show.run_show(vp) is True
                  and vp._sm.bailando.is_set())
        finally:
            music.ejecutar = original
    finally:
        show.time = _time

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
