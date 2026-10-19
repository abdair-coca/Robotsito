"""
test_jev_routing.py — Enrutamiento del turno con JEV (J2), sin red ni micrófono.

Simula decidir() (la ruta que devuelve JEV) y reemplaza el LLM, el TTS, el
micrófono y Spotify por registros. Cubre: la ruta de JEV manda, el respaldo
(None) usa los parsers de siempre, y el contexto del asistente según la ruta.

  python tests/unit/test_jev_routing.py
"""
import os
import sys
import threading
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from voz import pipeline as vp   # noqa: E402
from voz.jev_decision import Decision   # noqa: E402

# R1: el LLM adelantado se prueba en test_especulacion.py; aquí no debe pedir al real.
vp.JEV_EN_PARALELO = False

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
    def __init__(self):
        self.agregados = []

    def agregar(self, rec):
        self.agregados.append(rec)

    def vencidos(self):
        return []


def construir():
    v = vp.VoicePipeline(FakeMgr(), FakeSM())
    v._recordatorios = Recordatorios()
    v._hablar = lambda texto, emo=None: None
    v._grabar = lambda **k: None   # silencio → la conversación termina sola
    v.llamadas_llm = []
    v._stream_and_speak = lambda texto: v.llamadas_llm.append(texto)
    return v


def correr(v, texto, ruta_jev):
    """Ejecuta un turno con `texto` del usuario y la ruta que devuelve JEV (None = respaldo)."""
    v._transcribir = lambda audio: texto
    vp.decidir = lambda frase, ultimo: Decision(ruta_jev, 'CURIOSO', 0.9, 0.9, 0.3,
                                                ruta_jev is None, '')
    v._convo.clear()
    v._run_conversation(pending_audio=b'\x00' * 3200)


gate_original = vp.MUSICA_ENABLED
musica_original = vp.ejecutar_musica
decidir_original = vp.decidir
ejecutadas = []
try:
    vp.MUSICA_ENABLED = True
    vp.ejecutar_musica = lambda intent: (ejecutadas.append(intent.accion), "ok")[1]

    RECORDATORIO = "recuérdame en 5 minutos tomar agua"
    MUSICA = "pon algo de Queen"

    # 1) JEV dice recordatorio -> se crea el recordatorio, no va al LLM
    v = construir()
    correr(v, RECORDATORIO, 'recordatorio')
    check("recordatorio: JEV lo confirma -> se agenda y no va al LLM",
          len(v._recordatorios.agregados) == 1 and not v.llamadas_llm,
          f"-> agendados={len(v._recordatorios.agregados)} llm={len(v.llamadas_llm)}")

    # 2) JEV dice charla -> aunque las palabras clave digan recordatorio, no se agenda
    v = construir()
    correr(v, RECORDATORIO, 'charla')
    check("charla gana sobre palabras clave: no se agenda",
          not v._recordatorios.agregados and len(v.llamadas_llm) == 1,
          f"-> agendados={len(v._recordatorios.agregados)} llm={len(v.llamadas_llm)}")

    # 3) JEV no responde (None) -> parsers de siempre: se agenda
    v = construir()
    correr(v, RECORDATORIO, None)
    check("respaldo: parsers de siempre agendan",
          len(v._recordatorios.agregados) == 1, f"-> agendados={len(v._recordatorios.agregados)}")

    # 4) JEV dice musica -> se ejecuta la acción de Spotify
    ejecutadas.clear()
    v = construir()
    correr(v, MUSICA, 'musica')
    check("musica: JEV la confirma -> se ejecuta", ejecutadas == ['play'] and not v.llamadas_llm,
          f"-> acciones={ejecutadas}")

    # 5) JEV dice musica, pero el parser no saca acción -> cae a la charla
    ejecutadas.clear()
    v = construir()
    correr(v, "cuéntame un chiste", 'musica')
    check("musica sin acción del parser: cae a la charla",
          not ejecutadas and len(v.llamadas_llm) == 1, f"-> acciones={ejecutadas}")

    # 6) JEV dice charla -> no ejecuta música aunque el texto la pida
    ejecutadas.clear()
    v = construir()
    correr(v, MUSICA, 'charla')
    check("charla gana sobre música", not ejecutadas and len(v.llamadas_llm) == 1)

    # 7) Respaldo: música por palabras clave, como antes
    ejecutadas.clear()
    v = construir()
    correr(v, MUSICA, None)
    check("respaldo: música por palabras clave", ejecutadas == ['play'], f"-> acciones={ejecutadas}")

    # 8) La ruta queda disponible para el LLM
    v = construir()
    correr(v, "cuéntame algo", 'asistente')
    check("ruta guardada en el turno", v._ruta_turno == 'asistente')
finally:
    vp.MUSICA_ENABLED = gate_original
    vp.ejecutar_musica = musica_original
    vp.decidir = decidir_original

# 9) Contexto del asistente según la ruta (sin red: solo hora y fecha)
ctx_charla = vp._contexto_asistente_turno("¿qué hora es?", 'charla')
check("contexto: charla no inyecta datos", ctx_charla == '')
ctx_palabra = vp._contexto_asistente_turno("¿qué hora es?", None)
check("contexto: respaldo usa palabras clave", "Hora actual" in ctx_palabra)
ctx_asistente = vp._contexto_asistente_turno("cuéntame algo", 'asistente')
check("contexto: asistente sin palabra clave inyecta hora y fecha",
      "Hora actual" in ctx_asistente and "Fecha de hoy" in ctx_asistente)

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
