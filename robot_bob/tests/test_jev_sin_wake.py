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
        self.notificados = []
        self.saludos = []

    def notificar_wake_word(self, payload=None, saludar=True):
        self.notificados.append(payload)
        self.saludos.append(saludar)

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
    v._transcribir = lambda audio, **k: actual['texto']
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

# ── J3b: despedidas, saludo y respuesta inmediata ─────────────────────────────
def conversacion(v, turnos, pending_text=None, greeting=False, ruta_inicial=None):
    """Corre una charla completa. turnos: [(lo que oye Whisper, ruta de JEV)].
    pending_text: primer turno ya transcrito (ruta_inicial = lo que JEV dice de él).
    Devuelve (lo que dice Bob, veces que va al LLM, veces que identifica la cara)."""
    cola = list(turnos)
    actual = {'texto': pending_text, 'ruta': ruta_inicial}
    hablados, llm, ident = [], [], []

    def grabar(**k):
        if not cola:
            return None   # silencio → la charla termina
        actual['texto'], actual['ruta'] = cola.pop(0)
        return b'\x00' * 3200

    v._grabar = grabar
    v._transcribir = lambda audio, **k: actual['texto']
    vp.decidir = lambda frase, ultimo: Decision(actual['ruta'], 'CURIOSO', 0.9, 0.9, 0.3,
                                                actual['ruta'] is None, '')
    v._hablar = lambda texto, emo=None: hablados.append(texto)
    v._stream_and_speak = lambda texto: llm.append(texto)
    v._identificar = lambda: ident.append(1)
    v._run_conversation(pending_text=pending_text, greeting=greeting)
    return hablados, llm, ident


musica_gate = vp.MUSICA_ENABLED
musica_fn = vp.ejecutar_musica
try:
    vp.JEV_SIN_WAKE = True
    vp.MUSICA_ENABLED = True
    vp.ejecutar_musica = lambda intent: "ok"

    # 9) "vete" sin Bob no arranca la charla
    v = construir()
    correr(v, [("vete", 'despedida')])
    check("despedida sin Bob: no arranca la charla", v._sm.notificados == [],
          f"-> {v._sm.notificados}")

    # 10) "Bob, vete" sí arranca: el nombre gana
    v = construir()
    correr(v, [("Bob, vete", 'despedida')])
    check("despedida con Bob: arranca y se despide", len(v._sm.notificados) == 1)

    # 11) Activación por JEV no saluda; con "Bob" sí
    v = construir()
    correr(v, [("pon música de Queen", 'musica')])
    check("activación por JEV: sin saludo", v._sm.saludos == [False], f"-> {v._sm.saludos}")
    v = construir()
    correr(v, [("Bob, pon música de Queen", 'musica')])
    check("activación con Bob: con saludo", v._sm.saludos == [True], f"-> {v._sm.saludos}")

    # 12) Orden por JEV: va directo a la acción, sin identificar la cara ni hablar saludo
    v = construir()
    hablados, llm, ident = conversacion(v, [], pending_text="pon música de Queen",
                                        ruta_inicial='musica')
    check("orden por JEV: ejecuta sin identificar la cara",
          hablados == ["ok"] and ident == [] and llm == [],
          f"-> hablados={hablados} ident={len(ident)} llm={len(llm)}")

    # 13) Charla por JEV: identifica la cara justo antes de responder
    v = construir()
    hablados, llm, ident = conversacion(v, [], pending_text="cuéntame un chiste",
                                        ruta_inicial='charla')
    check("charla por JEV: identifica y luego responde",
          ident == [1] and llm == ["cuéntame un chiste"], f"-> ident={len(ident)} llm={llm}")

    # 14) Despedida en medio de la charla: se despide y no va al LLM
    v = construir()
    hablados, llm, ident = conversacion(v, [("adiós Bob, me tengo que ir", 'despedida')],
                                        pending_text="cuéntame un chiste", ruta_inicial='charla')
    check("despedida en charla: no responde con LLM, sí se despide",
          llm == ["cuéntame un chiste"] and len(hablados) == 1
          and hablados[0] in vp.DESPEDIDAS_JEV, f"-> hablados={hablados} llm={llm}")

    # 15) "ajeno" en medio de la charla: silencio, la charla sigue
    v = construir()
    hablados, llm, ident = conversacion(v, [("ruido de fondo", 'ajeno')],
                                        pending_text="cuéntame un chiste", ruta_inicial='charla')
    check("ajeno en charla: no contesta", llm == ["cuéntame un chiste"] and hablados == [],
          f"-> hablados={hablados} llm={llm}")

    # 16) Saludo con "Bob" sigue como antes: identifica al arranque
    v = construir()
    hablados, llm, ident = conversacion(v, [], pending_text="pon música de Queen", greeting=True,
                                        ruta_inicial='musica')
    check("con saludo: identifica al arranque", ident == [1], f"-> ident={len(ident)}")
finally:
    vp.MUSICA_ENABLED = musica_gate
    vp.ejecutar_musica = musica_fn

# ── J4: JEV elige la acción de música; el parser solo saca el detalle ─────────
acciones = []
musica_gate = vp.MUSICA_ENABLED
musica_fn = vp.ejecutar_musica
try:
    vp.MUSICA_ENABLED = True
    vp.ejecutar_musica = lambda intent: (acciones.append(intent), "ok")[1]

    def accion_de(texto, ruta):
        acciones.clear()
        v = construir()
        hablados, llm, _ = conversacion(v, [], pending_text=texto, ruta_inicial=ruta)
        return (acciones[0] if acciones else None), llm

    intent, llm = accion_de("pon otra música", 'musica_siguiente')
    check("J4: 'pon otra' con JEV siguiente -> next",
          intent is not None and intent.accion == 'next' and not llm, f"-> {intent}")

    intent, llm = accion_de("para un momento", 'musica_pausa')
    check("J4: JEV pausa -> pause", intent is not None and intent.accion == 'pause',
          f"-> {intent}")

    intent, llm = accion_de("pon el volumen al 70 por ciento", 'musica_volumen')
    check("J4: volumen exacto -> vol_set 70",
          intent is not None and intent.accion == 'vol_set' and intent.valor == 70, f"-> {intent}")

    intent, llm = accion_de("pon música de Taylor Swift", 'musica_play')
    check("J4: JEV play -> busca el artista",
          intent is not None and intent.accion == 'play' and intent.query == 'Taylor Swift',
          f"-> {intent}")

    intent, llm = accion_de("pon mi playlist de rock", 'musica_playlist')
    check("J4: JEV playlist -> play_playlist 'rock'",
          intent is not None and intent.accion == 'play_playlist' and intent.query == 'rock',
          f"-> {intent}")

    intent, llm = accion_de("sube el volumen", 'musica_subir')
    check("J4: JEV subir -> vol_up", intent is not None and intent.accion == 'vol_up',
          f"-> {intent}")

    intent, llm = accion_de("pon el volumen más alto", 'musica_volumen')
    check("J4: volumen sin número -> no ejecuta, pasa a la charla",
          intent is None and llm == ["pon el volumen más alto"], f"-> {intent} llm={llm}")

    # E-B: JEV dice play pero sin tema no se manda a la charla (el LLM inventaba que no
    # podía poner música): Bob pregunta "¿Qué canción quieres que ponga?" y no llama al LLM.
    intent, llm = accion_de("ponte cómodo", 'musica_play')
    check("J4 (E-B): JEV play sin tema claro → pregunta, sin LLM",
          intent is None and llm == [], f"-> {intent} llm={llm}")
finally:
    vp.MUSICA_ENABLED = musica_gate
    vp.ejecutar_musica = musica_fn

# ── J5: giro y presentación los decide JEV ────────────────────────────────────
show_gate = vp.run_show
mostro = []
try:
    vp.run_show = lambda v: (mostro.append(1), False)[1]   # no cierra la charla

    def giro_de(texto, ruta):
        v = construir()
        v._sm.scan_request = None
        hablados, llm, _ = conversacion(v, [], pending_text=texto, ruta_inicial=ruta)
        return v._sm.scan_request, llm

    scan, llm = giro_de("a tu derecha", 'giro_derecha')
    check("J5: JEV giro derecha -> scan 'derecha'", scan == 'derecha' and not llm, f"-> {scan}")

    scan, llm = giro_de("date la vuelta", 'giro_buscar')
    check("J5: JEV giro buscar -> scan 'buscar'", scan == 'buscar' and not llm, f"-> {scan}")

    scan, llm = giro_de("date la vuelta", 'charla')
    check("J5: JEV dice charla -> no gira aunque tenga la palabra",
          scan is None and llm == ["date la vuelta"], f"-> {scan} llm={llm}")

    scan, llm = giro_de("date la vuelta", None)
    check("J5: sin JEV -> palabras clave: 'buscar'", scan == 'buscar' and not llm, f"-> {scan}")

    mostro.clear()
    v = construir()
    hablados, llm, _ = conversacion(v, [], pending_text="preséntate", ruta_inicial='presentacion')
    check("J5: JEV presentación -> corre el show", len(mostro) == 1 and not llm,
          f"-> show={len(mostro)} llm={llm}")

    mostro.clear()
    v = construir()
    hablados, llm, _ = conversacion(v, [], pending_text="preséntate", ruta_inicial='charla')
    check("J5: JEV charla -> no corre el show", not mostro and llm == ["preséntate"],
          f"-> show={len(mostro)} llm={llm}")

    mostro.clear()
    v = construir()
    hablados, llm, _ = conversacion(v, [], pending_text="preséntate", ruta_inicial=None)
    check("J5: sin JEV -> palabras clave: corre el show", len(mostro) == 1,
          f"-> show={len(mostro)}")
finally:
    vp.run_show = show_gate

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
