"""
test_juego_charla.py — J1 en la charla: adivinanzas y trivia por voz, sin red ni micrófono.

Simula los turnos (grabación y transcripción) y reemplaza el LLM del juego, el TTS, el
LLM de la charla y JEV por registros. Cubre:
  1. "Bob, juguemos a la trivia" empieza; la respuesta se corrige y Bob lanza otra;
     "ya no quiero jugar" termina con el puntaje.
  2. Los turnos del juego no van al LLM de la charla ni a JEV (no se descartan como ajenos).
  3. Sin pedido de juego, el turno va a la charla de siempre.
  4. El juego no sobrevive la charla: en la siguiente, "Júpiter" es charla normal.
  5. Si el LLM del juego cae, Bob lo dice y no queda un juego a medias.
  6. Una despedida en medio del juego lo cierra sin "buen juego".

  python tests/unit/test_juego_charla.py
"""
import os
import sys
import threading
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from voz import pipeline as vp   # noqa: E402
from voz.jev_decision import Decision   # noqa: E402

vp.JEV_EN_PARALELO = False      # el LLM adelantado se prueba en test_especulacion.py
vp.JEV_ROUTING_ENABLED = True   # así se nota si un turno del juego pasa por JEV

fallos = 0


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
        self.musica_sonando = threading.Event()

    def estado_interno_prompt(self):
        return ''

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


class LLMFalso:
    """Devuelve las salidas en orden; sin más salidas, falla como un LLM caído."""

    def __init__(self, *salidas):
        self._salidas = list(salidas)

    def __call__(self, prompt):
        if not self._salidas:
            raise TimeoutError('sin más salidas')
        return self._salidas.pop(0)


PREGUNTAS = ('PREGUNTA: ¿Cuál es el planeta más grande?\nRESPUESTA: Júpiter',
             'PREGUNTA: ¿De qué color es el cielo?\nRESPUESTA: azul')


def construir(llm_juego):
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._recordatorios = types.SimpleNamespace(vencidos=lambda: [], agregar=lambda r: None,
                                             hay_vencidos=lambda: False)
    v.dichos, v.llamadas_llm, v.jev = [], [], []
    v._hablar = lambda texto, emo=None: v.dichos.append(texto)
    v._stream_and_speak = lambda texto: v.llamadas_llm.append(texto)
    v._pedir_jev = lambda texto: (v.jev.append(texto),
                                  Decision(None, 'CURIOSO', 0.9, 0.9, 0.3, True, ''))[1]
    v._llm_juego = llm_juego
    return v


def charla(v, *turnos):
    """Una charla con estos turnos del usuario; termina sola en silencio."""
    textos = list(turnos)
    v._transcribir = lambda *a, **k: textos.pop(0) if textos else ''
    v._grabar = lambda *a, **k: (b'\x00' * 3200) if textos else None
    v._convo.clear()
    v._run_conversation(pending_audio=b'\x00' * 3200)


# 1–2. Juego completo
v = construir(LLMFalso(*PREGUNTAS))
charla(v, 'Bob, juguemos a la trivia', 'Júpiter', 'ya no quiero jugar')
esperado = ['¡Va! ¿Cuál es el planeta más grande?',
            '¡Correcto! Llevas 1 punto(s). Va otra: ¿De qué color es el cielo?',
            '¡Buen juego! Hiciste 1 punto(s).']
check('juego: empieza, corrige, lanza otra y termina con el puntaje',
      v.dichos[:3] == esperado, f'-> {v.dichos}')
check('juego: no usa el LLM de la charla', not v.llamadas_llm, f'-> {v.llamadas_llm}')
check('juego: JEV no ve los turnos del juego', not v.jev, f'-> {v.jev}')
check('juego: al terminar no queda activo', v._juego is None)

# 3. Sin pedido de juego: charla de siempre
v = construir(LLMFalso())
charla(v, 'Bob, cuéntame algo de ti')
check('sin pedido de juego: el turno va a JEV y a la charla',
      len(v.llamadas_llm) == 1 and len(v.jev) == 1, f'-> llm={v.llamadas_llm} jev={v.jev}')

# 4. El juego no sobrevive la charla
v = construir(LLMFalso(*PREGUNTAS))
charla(v, 'juguemos a la trivia')
abierto = v._juego is not None
charla(v, 'Júpiter')
check('el juego no sobrevive la charla: "Júpiter" después es charla normal',
      abierto and len(v.llamadas_llm) == 1 and v._juego is None,
      f'-> abierto={abierto} llm={v.llamadas_llm}')

# 5. LLM del juego caído
v = construir(LLMFalso())
charla(v, 'dime una adivinanza')
check('LLM caído: Bob lo dice y no queda juego a medias',
      bool(v.dichos) and 'no puedo pensar' in v.dichos[0].lower() and v._juego is None,
      f'-> {v.dichos}')

# 6. Despedida en medio del juego
v = construir(LLMFalso(*PREGUNTAS))
charla(v, 'juguemos a la trivia', 'adiós')
check('una despedida cierra el juego sin "buen juego"',
      v._juego is None and not any('Buen juego' in d for d in v.dichos), f'-> {v.dichos}')

print('TODO OK' if fallos == 0 else f'{fallos} FALLA(S)', flush=True)
sys.exit(1 if fallos else 0)
