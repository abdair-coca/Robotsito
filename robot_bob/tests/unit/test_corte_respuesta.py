"""
test_corte_respuesta.py — Teclas D y J: cortar lo que Bob está diciendo.

Sin parlante, micrófono ni LLM real:
  1. Cortar a mitad de una respuesta en streaming: no dice el resto y no espera al LLM.
  2. En la charla, D escucha enseguida y J termina la charla.
  3. D sobre la frase que cerraba la charla vuelve a escuchar.
  4. D sobre una frase suelta (soliloquio) abre la escucha.
  5. Si Bob no está hablando ni pensando, las teclas no hacen nada.

  python tests/unit/test_corte_respuesta.py
"""
import os
import sys
import threading
import time
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from nucleo.state_machine import RobotState   # noqa: E402
from voz import pipeline as vp                # noqa: E402

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

    def __init__(self, estado=RobotState.SPEAKING):
        self.estado = estado
        self.ev_hablando = threading.Event()
        self.ev_escuchando = threading.Event()
        self.ev_pensando = threading.Event()
        self.bailando = threading.Event()
        self.musica_sonando = threading.Event()
        self.llamadas = []

    def estado_interno_prompt(self):
        return ''

    def iniciar_escuchando(self):
        self.llamadas.append('escuchar')

    def fin_turno(self, pending=None):
        self.llamadas.append('fin_turno')

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeLLM:
    """Tres frases; la tercera tarda (si el corte esperara al LLM, se notaría)."""

    def __init__(self):
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        class S:
            def __iter__(s):
                yield NS(choices=[NS(delta=NS(content='Primera frase de la respuesta. '))])
                yield NS(choices=[NS(delta=NS(content='Segunda frase de la respuesta. '))])
                time.sleep(4.0)
                yield NS(choices=[NS(delta=NS(content='Tercera frase de la respuesta.'))])

            def close(s):
                pass
        return S()


def construir(estado=RobotState.SPEAKING):
    return vp.VoicePipeline(FakeMgr(), FakeSM(estado))


def charla(v, turnos, cortar_con):
    """Corre una charla: cada turno de `turnos` es lo que dice el usuario; la
    respuesta de Bob se corta con D (True) o J (False)."""
    eventos = []
    v._tono_escucha = lambda: None

    def grabar(**k):
        eventos.append('grabar')
        return b'wav' if turnos else None

    def transcribir(audio, **k):
        v._stt_descartado, v._stt_fallo, v._stt_dudosa = '', False, False
        return turnos.pop(0) if turnos else ''

    def responder(texto):
        eventos.append('responde')
        v.cortar_respuesta(escuchar=cortar_con)
    v._grabar, v._transcribir, v._stream_and_speak = grabar, transcribir, responder
    v._run_conversation()
    return eventos


def main() -> None:
    synth_original = vp._sintetizar
    vp._sintetizar = lambda t, e=None: t.encode()
    try:
        # 1) Corte a mitad de una respuesta en streaming
        v = construir()
        v._llm = FakeLLM()
        dichas = []

        def reproducir(audio):
            dichas.append(audio.decode())
            v.cortar_respuesta(escuchar=True)     # D mientras dice la 1ª frase
        v._reproducir = reproducir
        t0 = time.monotonic()
        v._stream_and_speak('cuéntame algo')
        dur = time.monotonic() - t0
        check('streaming: solo dice la frase en curso', dichas == ['Primera frase de la respuesta.'],
              f'-> {dichas}')
        check('streaming: vuelve sin esperar al LLM', dur < 1.5, f'({dur:.2f} s)')
        check('streaming: el corte queda para la charla', v._cortado() and v._corte_escuchar)
    finally:
        vp._sintetizar = synth_original

    # _reproducir con corte pendiente: no suena nada
    tocado = []
    v._pygame = NS(sndarray=NS(make_sound=lambda a: tocado.append(a)))
    check('reproducir: con corte pendiente no suena', v._reproducir(b'RIFF') is None and not tocado)

    # 2) En la charla: D escucha enseguida, J termina la charla
    v = construir()
    eventos = charla(v, ['hola', 'qué tal'], cortar_con=True)
    check('charla D: tras cortar vuelve a escuchar',
          eventos[:4] == ['grabar', 'responde', 'grabar', 'responde'], f'-> {eventos}')
    check('charla D: el corte se consume', not v._cortado())
    v = construir()
    eventos = charla(v, ['hola', 'qué tal'], cortar_con=False)
    check('charla J: tras cortar no escucha más', eventos == ['grabar', 'responde'], f'-> {eventos}')
    check('charla J: termina la charla', v._sm.llamadas == ['fin_turno'] and not v._cortado(),
          f'-> {v._sm.llamadas}')

    # 3) D sobre la frase que cerraba la charla → escuchar de nuevo
    v = construir()
    v._grabar = lambda **k: b'wav'
    v._transcribir = lambda audio, **k: 'adiós'
    v._stt_descartado, v._stt_fallo, v._stt_dudosa = '', False, False
    v._hablar = lambda texto, emo=None: v.cortar_respuesta(escuchar=True)
    v._tono_escucha = lambda: None
    v._run_conversation()
    check('cierre D: vuelve a escuchar en vez de cerrar', v._sm.llamadas == ['escuchar'],
          f'-> {v._sm.llamadas}')

    # 4) D sobre una frase suelta (soliloquio): abre la escucha
    v = construir(RobotState.IDLE)
    v._hablar = lambda texto, emo=None: v.cortar_respuesta(escuchar=True)
    v.decir_soliloquio('[EMO:FELIZ] Qué lindo día hace hoy.')
    check('soliloquio D: se pone a escuchar', v._sm.llamadas == ['escuchar'] and not v._cortado(),
          f'-> {v._sm.llamadas}')
    v = construir(RobotState.IDLE)
    v._hablar = lambda texto, emo=None: v.cortar_respuesta(escuchar=False)
    v.decir_soliloquio('[EMO:FELIZ] Qué lindo día hace hoy.')
    check('soliloquio J: solo se calla', v._sm.llamadas == [] and not v._cortado(),
          f'-> {v._sm.llamadas}')

    # 5) Sin respuesta en curso, las teclas no hacen nada
    for estado in (RobotState.LISTENING, RobotState.IDLE):
        v = construir(estado)
        v.cortar_respuesta(escuchar=True)
        v.cortar_respuesta(escuchar=False)
        check(f'callado ({estado.name}): no queda corte pendiente', not v._cortado())

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
