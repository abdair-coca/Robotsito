"""
test_estado_cli.py — Estado y turnos para el CLI (T4 del plan del CLI).

Sin hardware, red ni CLI:
  1. estado_cli.foto() lee FSM, emoción, micrófono, música, baile y modo ingeniero.
  2. El hilo emite la foto entera la primera vez y después solo lo que cambió.
  3. Sin modo CLI el hilo no arranca.
  4. El pipeline marca como turno las líneas «Usuario:» y las frases de Bob.

  python tests/unit/test_estado_cli.py
"""
import contextlib
import os
import sys
import threading
import time
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from nucleo import estado_cli, puente_cli   # noqa: E402
from nucleo.state_machine import RobotState   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


sm = NS(estado=RobotState.IDLE, musica_sonando=threading.Event(), bailando=threading.Event())
voice = NS(microfono_activo=True, _modo_ingeniero=False)
serial = NS(_ultimo_estado='')

# ── 1. foto() ──────────────────────────────────────────────────────────────────
check('foto: sin emoción todavía no la informa', estado_cli.foto(sm, voice, serial) == {
    'fsm': 'IDLE', 'mic': True, 'musica': False, 'baile': False, 'ingeniero': False})
serial._ultimo_estado = 'FELIZ'
sm.musica_sonando.set()
voice._modo_ingeniero = True
check('foto: emoción, música y modo ingeniero', estado_cli.foto(sm, voice, serial) == {
    'fsm': 'IDLE', 'mic': True, 'musica': True, 'baile': False, 'ingeniero': True, 'emocion': 'FELIZ'})
check('cambios: solo lo distinto', estado_cli.cambios({'fsm': 'IDLE', 'mic': True},
                                                      {'fsm': 'THINKING', 'mic': True}) == {'fsm': 'THINKING'})

# ── 2-3. El hilo ───────────────────────────────────────────────────────────────
emitidos = []
emitir_original, activo_original = puente_cli.emitir, puente_cli.ACTIVO
puente_cli.emitir = emitidos.append
detener = threading.Event()
puente_cli.ACTIVO = False
check('sin modo CLI: no arranca', estado_cli.iniciar(sm, voice, serial, detener) is False)
puente_cli.ACTIVO = True
check('con modo CLI: arranca', estado_cli.iniciar(sm, voice, serial, detener) is True)
time.sleep(0.5)
sm.estado = RobotState.THINKING
voice.microfono_activo = False
time.sleep(0.5)
sm.bailando.set()
time.sleep(0.5)
detener.set()
time.sleep(0.3)
puente_cli.emitir, puente_cli.ACTIVO = emitir_original, activo_original
check('hilo: primero la foto entera', emitidos and emitidos[0] == {
    't': 'estado', 'fsm': 'IDLE', 'mic': True, 'musica': True, 'baile': False, 'ingeniero': True,
    'emocion': 'FELIZ'}, repr(emitidos[:1]))
check('hilo: después solo lo que cambió, sin repetir', emitidos[1:] == [
    {'t': 'estado', 'fsm': 'THINKING', 'mic': False}, {'t': 'estado', 'baile': True}], repr(emitidos[1:]))

# ── 4. Turnos marcados en el pipeline ──────────────────────────────────────────
from voz import pipeline as vp   # noqa: E402

marcas = []   # (turno, texto impreso adentro)
print_original = vp.console.print


@contextlib.contextmanager
def turno_registrado(quien):
    vp.console.print = lambda *a, **k: marcas.append((quien, str(a[0]) if a else ''))
    try:
        yield
    finally:
        vp.console.print = print_original


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
        self.musica_sonando = threading.Event()

    def estado_interno_prompt(self):
        return ''

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeLLM:
    def __init__(self):
        self.chat = NS(completions=NS(create=self.create))

    def create(self, **kw):
        class S:
            def __iter__(s):
                yield NS(choices=[NS(delta=NS(content='Hola, soy Bob y te escucho.'))])

            def close(s):
                pass
        return S()


turno_original = puente_cli.turno
puente_cli.turno = turno_registrado
synth_original = vp._sintetizar
vp._sintetizar = lambda t, e=None: t.encode()
vp.JEV_EN_PARALELO = False
try:
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._llm = FakeLLM()
    v._reproducir = lambda audio: None
    v._stream_and_speak('hola')
    check('streaming: la frase de Bob va como turno bob',
          any(q == 'bob' and 'Hola, soy Bob' in t for q, t in marcas), repr(marcas))

    marcas.clear()
    turnos = ['hola Bob']
    v._tono_escucha = lambda: None
    v._grabar = lambda **k: b'wav' if turnos else None

    def transcribir(audio, **k):
        v._stt_descartado, v._stt_fallo, v._stt_dudosa = '', False, False
        return turnos.pop(0) if turnos else ''
    v._transcribir = transcribir
    v._stream_and_speak = lambda texto: None
    v._run_conversation()
    check('charla: «Usuario:» va como turno usuario',
          ('usuario', '[bold cyan]Usuario:[/] hola Bob') in marcas, repr(marcas))
finally:
    puente_cli.turno = turno_original
    vp._sintetizar = synth_original

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLO(S)'}")
sys.exit(1 if fallos else 0)
