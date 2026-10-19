"""
test_jev_emocion.py — Emoción del turno decidida por JEV (J3), sin red ni micrófono.

Simula decidir() (la emoción que devuelve JEV) y un LLM que sí escribe tags
[EMO:X], para comprobar que Bob usa la emoción de JEV en cada frase, quita los
tags del LLM y no los guarda en el historial. Sin JEV, la emoción es CURIOSO.

  python tests/test_jev_emocion.py
"""
import os
import sys
import threading
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from voz import pipeline as vp   # noqa: E402
from voz.jev_decision import Decision, EMOCION_RESPALDO   # noqa: E402

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

    def estado_interno_prompt(self):
        return ''

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


class Recordatorios:
    def agregar(self, rec):
        pass

    def vencidos(self):
        return []


class FakeLLM:
    """Imita chat.completions.create en modo stream: un chunk por trozo de texto."""
    def __init__(self, trozos):
        self.trozos = trozos
        self.chat = self
        self.completions = self

    def create(self, **k):
        return iter([types.SimpleNamespace(
            choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=t))])
            for t in self.trozos])


def construir():
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._recordatorios = Recordatorios()
    v._system_prompt_actual = 'sistema de prueba'
    v._llm_model = 'fake'
    v._llm_extra = {}
    v._convo.clear()
    return v


decidir_original = vp.decidir
routing_original = vp.JEV_ROUTING_ENABLED
try:
    # 1) El marcado pone la emoción de JEV y quita los tags que escribe el LLM
    check("_marcar_emo: pone el tag de JEV",
          vp._marcar_emo("Hola amigo.", "TRISTE") == "[EMO:TRISTE] Hola amigo.")
    check("_marcar_emo: quita el tag del LLM y pone el de JEV",
          vp._marcar_emo("[EMO:FELIZ] Hola.", "AMOR") == "[EMO:AMOR] Hola.")

    # 2) El LLM escribe tags distintos: todas las frases salen con la emoción de JEV
    v = construir()
    v._llm = FakeLLM(["[EMO:FELIZ] Qué bueno ", "verte. [EMO:CURIOSO] ¿Qué tal", " estás?"])
    v._emocion_turno = 'TRISTE'
    v._ruta_turno = None
    salida = list(v._stream_llm("hola"))
    check("_stream_llm: cada frase lleva la emoción de JEV",
          bool(salida) and all(s.startswith('[EMO:TRISTE] ') for s in salida),
          f"-> {salida}")
    check("_stream_llm: no sobreviven los tags del LLM",
          not any('FELIZ' in s or 'CURIOSO' in s for s in salida))
    check("_stream_llm: el historial guarda el texto sin tags",
          bool(v._convo) and '[EMO' not in v._convo[-1]['content'],
          f"-> {v._convo[-1]['content'] if v._convo else ''}")

    # 3) JEV decide la emoción del turno y la ruta (sin decisión previa)
    vp.JEV_ROUTING_ENABLED = True
    llamadas = []
    vp.decidir = lambda frase, ultimo='': (llamadas.append(frase),
                                            Decision('charla', 'ORGULLOSO', 0.9, 0.9, 0.3, False, ''))[1]
    v = construir()
    ruta = v._decidir_ruta("cuéntame algo")
    check("JEV: la emoción del turno sale de JEV",
          v._emocion_turno == 'ORGULLOSO' and ruta == 'charla',
          f"-> ruta={ruta} emoción={v._emocion_turno}")

    # 4) Si el monitor en reposo ya pidió la decisión para este texto, no se vuelve a pedir
    v = construir()
    v._jev_previa = ("cuéntame algo", Decision('charla', 'TRAVIESO', 0.9, 0.9, 0.3, False, ''))
    llamadas.clear()
    ruta = v._decidir_ruta("cuéntame algo")
    check("JEV previa: reutiliza la emoción sin volver a preguntar",
          v._emocion_turno == 'TRAVIESO' and ruta == 'charla' and not llamadas,
          f"-> emoción={v._emocion_turno} llamadas={len(llamadas)}")

    # 5) JEV apagado: sin ruta y con la emoción de respaldo
    vp.JEV_ROUTING_ENABLED = False
    v = construir()
    ruta = v._decidir_ruta("hola")
    check("JEV apagado: ruta None y emoción de respaldo",
          ruta is None and v._emocion_turno == EMOCION_RESPALDO == 'CURIOSO',
          f"-> ruta={ruta} emoción={v._emocion_turno}")
finally:
    vp.decidir = decidir_original
    vp.JEV_ROUTING_ENABLED = routing_original

print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
