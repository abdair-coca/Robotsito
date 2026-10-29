"""
test_escenas_audio.py — Audio de las teclas 1 a 9 listo desde el arranque, sin Piper ni parlante.

Piper tarda ~1–2 s por frase (y ~7 s la primera): las frases fijas se sintetizan al arrancar.
  1. La precarga sintetiza cada frase de ESCENAS una vez, con su cara, frases sueltas primero.
  2. _hablar usa el audio listo sin sintetizar; una frase nueva se sintetiza como antes.
  3. Si Piper falla, la precarga avisa y para sin romper nada.
  4. Con Bob cerrando, la precarga no sintetiza.

  python tests/unit/test_escenas_audio.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from nucleo.modos import ESCENAS, FRASES   # noqa: E402
from voz import pipeline as vp   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class FakeSM:
    en_conversacion = False

    def __init__(self):
        self.ev_hablando = threading.Event()
        self.ev_escuchando = threading.Event()
        self.ev_pensando = threading.Event()
        self.bailando = threading.Event()
        self.musica_sonando = threading.Event()

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


sintetizar_original = vp._sintetizar
pedidos = []


def sintetizar_falso(texto, emo=None):
    pedidos.append((texto, emo))
    return f'audio:{texto}'.encode()


try:
    vp._sintetizar = sintetizar_falso

    # ── 1. Precarga ─────────────────────────────────────────────────────────────
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._precargar_escenas()
    todas = [(f, c) for pasos in ESCENAS.values() for f, c, _ in pasos]
    check('una síntesis por frase, con su cara', sorted(pedidos) == sorted(set(todas)),
          f'-> {len(pedidos)} de {len(set(todas))}')
    sueltas = {pasos[0][0] for pasos in FRASES.values()}
    check('frases sueltas primero', {f for f, _ in pedidos[:len(sueltas)]} == sueltas)
    check('todo queda listo', set(v._escenas_audio) == {f for f, _ in todas})

    # ── 2. _hablar ──────────────────────────────────────────────────────────────
    sonado = []
    v._reproducir = sonado.append
    pedidos.clear()
    saludo = FRASES['saludo'][0][0]
    v._hablar(saludo, 'FELIZ')
    check('frase lista: suena sin sintetizar', pedidos == [] and sonado == [f'audio:{saludo}'.encode()])
    v._hablar('Algo que no es de una tecla.', 'CURIOSO')
    check('frase nueva: se sintetiza', pedidos == [('Algo que no es de una tecla.', 'CURIOSO')])

    # ── 3. Piper falla ──────────────────────────────────────────────────────────
    def explota(texto, emo=None):
        raise RuntimeError('sin modelo')

    vp._sintetizar = explota
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._precargar_escenas()
    check('Piper falla: para sin romper y sin audio', v._escenas_audio == {})

    # ── 4. Cerrando ─────────────────────────────────────────────────────────────
    vp._sintetizar = sintetizar_falso
    pedidos.clear()
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._detener.set()
    v._precargar_escenas()
    check('cerrando: no sintetiza', pedidos == [])
finally:
    vp._sintetizar = sintetizar_original

print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
