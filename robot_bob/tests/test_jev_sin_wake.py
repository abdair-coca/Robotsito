"""
test_jev_sin_wake.py — Monitor en reposo con JEV (J3), sin red ni micrófono.

Cada frase que oye el monitor pasa por JEV: si JEV dice que le habla a Bob, arranca
la charla sin que diga "Bob". Si JEV dice "ajeno" o no responde, no arranca (salvo
que haya dicho "Bob"). Simula la grabación, Whisper y JEV; el detector de "Bob" es el real.

  python tests/test_jev_sin_wake.py
"""
import os
import sys
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import voice_pipeline as vp   # noqa: E402
from jev_decision import Decision   # noqa: E402

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
        self.notificados = []

    def notificar_wake_word(self, payload=None):
        self.notificados.append(payload)

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


def construir():
    return vp.VoicePipeline(FakeMgr(), FakeSM())


def correr(v, turnos):
    """turnos: [(lo que oye Whisper, ruta de JEV)]. Ruta None = JEV no respondió.
    Devuelve cuántas veces se llamó a JEV."""
    cola = list(turnos)
    actual = {}
    llamadas = []

    def grabar(**k):
        if not cola:
            v._detener.set()
            return None
        actual['texto'], actual['ruta'] = cola.pop(0)
        return b'\x00' * 3200

    def decidir_falso(frase, ultimo):
        llamadas.append(frase)
        return Decision(actual['ruta'], 'CURIOSO', 0.9, 0.9, 0.3, actual['ruta'] is None, '')

    v._grabar = grabar
    v._grabar_wake = grabar   # camino de respaldo (JEV_SIN_WAKE desactivado)
    v._transcribir = lambda audio: actual['texto']
    vp.decidir = decidir_falso
    v._wake_monitor_loop()
    return llamadas


sin_wake_original = vp.JEV_SIN_WAKE
decidir_original = vp.decidir
try:
    vp.JEV_SIN_WAKE = True

    # 1) Sin "Bob": JEV dice musica -> arranca la charla con la frase completa
    v = construir()
    llamadas = correr(v, [("pon algo de Queen", 'musica')])
    check("sin Bob: JEV le habla -> arranca la charla",
          v._sm.notificados == ["pon algo de Queen"], f"-> {v._sm.notificados}")
    check("sin Bob: JEV se llama una sola vez", len(llamadas) == 1, f"-> {len(llamadas)}")
    ruta = v._decidir_ruta("pon algo de Queen")
    check("el primer turno reutiliza la decisión del monitor",
          ruta == 'musica' and len(llamadas) == 1, f"-> ruta={ruta} llamadas={len(llamadas)}")
    v._jev_previa = ("otra cosa", Decision('charla', 'CURIOSO', 0.9, 0.9, 0.3, False, ''))
    v._decidir_ruta("pon algo de Queen")
    check("texto distinto no reutiliza la decisión (llama a JEV)",
          len(llamadas) == 2, f"-> llamadas={len(llamadas)}")

    # 2) Sin "Bob": JEV dice ajeno -> Bob se queda callado
    v = construir()
    correr(v, [("nos vemos a las cinco en la cafetería", 'ajeno')])
    check("ajeno sin Bob: no arranca la charla", v._sm.notificados == [],
          f"-> {v._sm.notificados}")

    # 3) "Bob" + ajeno: el nombre gana -> arranca
    v = construir()
    correr(v, [("Bob, ¿qué haces?", 'ajeno')])
    check("ajeno pero con Bob: arranca la charla", len(v._sm.notificados) == 1,
          f"-> {v._sm.notificados}")

    # 4) JEV no responde y no dijo "Bob" -> no arranca (respaldo = pedir Bob)
    v = construir()
    correr(v, [("pon algo de Queen", None)])
    check("JEV caído sin Bob: no arranca", v._sm.notificados == [],
          f"-> {v._sm.notificados}")

    # 5) JEV no responde pero dijo "Bob" -> arranca como antes
    v = construir()
    correr(v, [("Bob, pon música", None)])
    check("JEV caído con Bob: arranca (respaldo)", len(v._sm.notificados) == 1,
          f"-> {v._sm.notificados}")

    # 6) Solo "Bob" -> saludo como antes, sin llamar a JEV
    v = construir()
    llamadas = correr(v, [("Bob", 'charla')])
    check("solo Bob: arranca sin payload y sin llamar a JEV",
          v._sm.notificados == [None] and llamadas == [],
          f"-> {v._sm.notificados} llamadas={len(llamadas)}")

    # 7) Varias frases seguidas: solo las que JEV reconoce arrancan
    v = construir()
    correr(v, [("ruido de fondo", 'ajeno'), ("cuéntame un chiste", 'charla')])
    check("varias frases: solo la dirigida a Bob arranca",
          v._sm.notificados == ["cuéntame un chiste"], f"-> {v._sm.notificados}")

    # 8) Interruptor apagado: sin Bob no arranca y JEV no se llama (como antes)
    vp.JEV_SIN_WAKE = False
    v = construir()
    llamadas = correr(v, [("pon algo de Queen", 'musica')])
    check("JEV_SIN_WAKE=False: sin Bob no arranca y no llama a JEV",
          v._sm.notificados == [] and llamadas == [],
          f"-> {v._sm.notificados} llamadas={len(llamadas)}")
    vp.JEV_SIN_WAKE = True
finally:
    vp.JEV_SIN_WAKE = sin_wake_original
    vp.decidir = decidir_original

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
