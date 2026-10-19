"""
test_interaccion.py — Interacción de la Fase R4, sin parlante ni micrófono.

Cubre: la frase de relleno cuando la respuesta tarda (y no cuando es rápida o está
apagada), el tono de escucha (antes de grabar si Bob habló; no tras un ruido ni sin
micrófono continuo; marcado como voz de Bob con margen corto) y la reproducción de
Piper como WAV en memoria (sin mp3 ni archivo temporal, mono→estéreo) con un
pygame falso; y que el camino mp3 descarga el archivo antes de borrarlo.

  python tests/test_interaccion.py
"""
import os
import sys
import threading
import time
from types import SimpleNamespace as NS

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from voz import pipeline as vp   # noqa: E402
from voz import audio_engine as ae     # noqa: E402

fallos = 0
vp.JEV_EN_PARALELO = False


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class FakeSM:
    en_conversacion = False
    stt_fail_streak = 0
    positive_streak = 0
    mood = 0.0

    def __init__(self):
        self.ev_hablando = threading.Event()
        self.ev_escuchando = threading.Event()
        self.ev_pensando = threading.Event()
        self.bailando = threading.Event()

    def estado_interno_prompt(self):
        return ''

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeLLM:
    def __init__(self, demora=0.0):
        self.demora = demora
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        llm = self

        class S:
            def __iter__(s):
                time.sleep(llm.demora)
                yield NS(choices=[NS(delta=NS(content='Claro que sí, te cuento algo bonito.'))])

            def close(s):
                pass
        return S()


class FakePygame:
    """Registra qué API de pygame se usó para reproducir."""

    def __init__(self, canales=2):
        self.llamadas = []
        self.forma = None
        pg = self

        class Canal:
            def get_busy(s):
                return False

            def stop(s):
                pass

        class Sonido:
            def play(s):
                pg.llamadas.append('sound.play')
                return Canal()

        def make_sound(arr):
            pg.forma = arr.shape
            pg.llamadas.append('make_sound')
            return Sonido()
        self.sndarray = NS(make_sound=make_sound)
        self.mixer = NS(get_init=lambda: (24000, -16, canales),
                        music=NS(load=lambda p: self.llamadas.append(('load', p)),
                                 play=lambda: self.llamadas.append('play'),
                                 get_busy=lambda: False, stop=lambda: None,
                                 unload=lambda: self.llamadas.append('unload')))


def construir():
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v.reproducido = []
    v._reproducir = lambda audio: v.reproducido.append(audio) and None
    return v


def main() -> None:
    synth_original = vp._sintetizar
    vp._sintetizar = lambda t, e=None: t.encode()
    try:
        # 1) Relleno: respuesta lenta → "Mmm, a ver…" antes; rápida → nada
        v = construir()
        v._llm = FakeLLM(demora=2.2)
        v._stream_and_speak('cuéntame algo')
        textos = [a.decode() for a in v.reproducido]
        check('lenta: primero el relleno', len(textos) == 2 and textos[0] in vp.RELLENOS, f'-> {textos}')
        check('lenta: después la respuesta', textos and textos[-1].startswith('Claro que sí'))
        v = construir()
        v._llm = FakeLLM(demora=0.3)
        v._stream_and_speak('cuéntame algo')
        check('rápida: sin relleno', [a.decode() for a in v.reproducido] == ['Claro que sí, te cuento algo bonito.'])
        vp.RELLENO_ENABLED = False
        v = construir()
        v._llm = FakeLLM(demora=2.2)
        v._stream_and_speak('cuéntame algo')
        check('apagado: sin relleno aunque tarde', len(v.reproducido) == 1)
        vp.RELLENO_ENABLED = True
    finally:
        vp._sintetizar = synth_original

    # 2) Tono de escucha: con micrófono continuo, marcado como voz de Bob (margen corto)
    v = construir()
    pg = FakePygame()
    v._pygame = pg
    v._mic = mic = ae.Microfono(NS(prob=lambda b: 0.0), abrir=False, log=lambda m: None)
    marcas = []
    real_bob = mic.bob_habla
    mic.bob_habla = lambda activo, margen_ms=ae.ECO_MARGEN_MS: (marcas.append((activo, margen_ms)),
                                                                  real_bob(activo, margen_ms))
    v._tono_escucha()
    check('tono: suena (WAV en memoria)', pg.llamadas[:2] == ['make_sound', 'sound.play'], f'-> {pg.llamadas}')
    check('tono: ~120 ms y estéreo para el mixer', pg.forma and pg.forma[1] == 2
          and 0.10 <= pg.forma[0] / 24000 <= 0.14, f'-> {pg.forma}')
    check('tono: marcado como voz de Bob, margen 60 ms', marcas == [(True, ae.ECO_MARGEN_MS), (False, 60)],
          f'-> {marcas}')
    v2 = construir()
    v2._pygame = pg2 = FakePygame()
    v2._tono_escucha()
    check('tono: sin micrófono continuo no suena', not pg2.llamadas)

    # 3) En la charla: tono antes de grabar si Bob habló; no tras un ruido
    v = construir()
    v._mic = NS(bob_habla=lambda *a, **k: None)
    eventos = []
    v._tono_escucha = lambda: eventos.append('tono')
    turnos = ['', 'hola']            # 1º: ruido descartado; 2º: frase

    def grabar(**k):
        eventos.append('grabar')
        return b'wav' if turnos else None

    def transcribir(audio, **k):
        t = turnos.pop(0) if turnos else ''
        v._stt_descartado = '' if t else 'ruido'
        v._stt_fallo = v._stt_dudosa = False
        return t
    v._grabar, v._transcribir = grabar, transcribir
    v._stream_and_speak = lambda texto: (eventos.append('responde'), v.__setattr__('_hablo_desde_escucha', True))
    v._run_conversation()
    check('charla: tono al empezar, no tras el ruido, sí tras responder',
          eventos[:5] == ['tono', 'grabar', 'grabar', 'responde', 'tono'], f'-> {eventos}')

    # 4) Piper → WAV 24 kHz en memoria; reproducción sin archivo temporal
    wav = vp._piper_audio('Hola, soy Bob.')
    check('piper: WAV (no mp3)', wav[:4] == b'RIFF', f'-> {wav[:4]!r}')
    import wave
    import io
    with wave.open(io.BytesIO(wav)) as wf:
        sr, dur = wf.getframerate(), wf.getnframes() / wf.getframerate()
    check('piper: 24 kHz mono, duración razonable', sr == 24000 and 0.5 < dur < 3.0, f'-> {sr} Hz, {dur:.2f} s')
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._pygame = pg = FakePygame()
    v._reproducir_laptop(wav)
    check('WAV: sin mixer.music ni archivo', 'make_sound' in pg.llamadas
          and not any(isinstance(c, tuple) for c in pg.llamadas))
    v._pygame = pg = FakePygame(canales=1)
    v._reproducir_laptop(wav)
    check('WAV: mixer mono → sin duplicar canales', pg.forma is not None and len(pg.forma) == 1)

    # 5) Camino mp3 (edge-tts): descarga antes de borrar → no quedan temporales
    v._pygame = pg = FakePygame()
    v._reproducir_laptop(b'ID3falso-mp3')
    ruta = next(c[1] for c in pg.llamadas if isinstance(c, tuple))
    check('mp3: unload antes de borrar', 'unload' in pg.llamadas and not os.path.exists(ruta))

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
