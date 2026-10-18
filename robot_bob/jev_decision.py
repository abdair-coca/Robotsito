"""
jev_decision.py — Decisiones de Bob con el modelo JEV (solo texto), vía typesafe_sdk.

Dos peticiones en paralelo, cada una con una sola pregunta:
  - ruta: musica | recordatorio | asistente | charla | despedida | ajeno
    (None si JEV no respondió)
  - emocion: una de EMOCIONES para la respuesta (CURIOSO si no respondió)

Medido en J1: una petición con las dos preguntas tarda ~1.0 s (mediana); cada
pregunta por separado en paralelo tarda ~0.5 s. Por eso van separadas.

El SDK no reintenta (RetryPolicy(max_retries=0)) para que el plazo de
JEV_TIMEOUT_S sea real. Si JEV no responde a tiempo, falla o responde algo
inválido, devuelve el respaldo para la parte que falló. Nunca lanza excepciones
hacia el pipeline.
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

from config import JEV_MODEL, JEV_TIMEOUT_S, TYPESAFE_API_KEY

EMOCION_RESPALDO = "CURIOSO"

# J4: una línea JSON por decisión. Las pruebas la cambian a un archivo temporal.
LOG_DECISIONES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "decisiones.jsonl")
_registro_lock = threading.Lock()

RUTAS = {
    "musica": "Pide algo de música que no encaja en las rutas de música específicas",
    "musica_play": "Pide poner una canción, artista o género: pon X, toca X, pon música de X",
    "musica_playlist": "Pide poner una playlist: mi playlist de rock, mis playlists",
    "musica_reanudar": "Quiere seguir la música que ya estaba: reanuda, sigue, dale play",
    "musica_pausa": "Quiere pausar o parar la música",
    "musica_siguiente": "Quiere la canción siguiente u otra: pon otra, salta, siguiente",
    "musica_anterior": "Quiere la canción anterior",
    "musica_subir": "Quiere más volumen: sube el volumen, más fuerte",
    "musica_bajar": "Quiere menos volumen: baja el volumen, más bajo",
    "musica_volumen": "Quiere un volumen exacto: pon el volumen al 70 por ciento",
    "recordatorio": "Pide un recordatorio, temporizador o alarma",
    "asistente": "Pregunta por hora, fecha, clima o noticias",
    "charla": "Cualquier otra cosa: conversar, preguntar, pedir un chiste",
    "giro_derecha": "Quiere que Bob gire o mire hacia su derecha: a tu derecha, por la derecha",
    "giro_izquierda": "Quiere que Bob gire o mire hacia su izquierda: a tu izquierda, por la izquierda",
    "giro_buscar": "Quiere que Bob se dé la vuelta o busque a quien le habla: date la vuelta, "
                   "estoy detrás de ti, gira",
    "presentacion": "Quiere que Bob haga su presentación o muestre lo que sabe hacer: "
                    "preséntate, qué sabes hacer, muéstrame lo que sabes",
    "despedida": "Quiere que Bob se vaya, se calle o termine: adiós, me voy, vete, "
                 "no me hables, déjame en paz",
    "ajeno": "Claramente no es para Bob: ruido o gente hablando entre sí sin nombrarlo. "
             "Ante la duda, NO elijas ajeno",
}

EMOCIONES = {
    "FELIZ": "Alegría normal; algo bueno pasó o el usuario saluda con cariño",
    "MUY_FELIZ": "Muy contento, entusiasmo fuerte",
    "CURIOSO": "Interés, ganas de saber más, pregunta del usuario",
    "TRAVIESO": "Juguetón, bromista, una broma o un chiste",
    "AMOR": "Cariño, afecto, halago o agradecimiento sincero",
    "CONFUNDIDO": "No entendió bien, pregunta ambigua o confusa",
    "SORPRENDIDO": "Algo inesperado o sorprendente",
    "TRISTE": "Tristeza leve, el usuario se queja o cuenta algo triste",
    "MUY_TRISTE": "Tristeza fuerte, pérdida o malas noticias graves",
    "ORGULLOSO": "Orgullo por un logro, propio o del usuario",
    "EMOCIONADO": "Ilusión por algo que viene o se va a hacer",
    "SOSPECHANDO": "Duda o desconfianza ante algo raro",
    "ENOJADO": "Molestia, el usuario es grosero o insiste en algo malo",
    "AVERGONZADO": "Vergüenza, Bob se equivocó o lo corrigieron",
    "ASUSTADO": "Miedo o susto ante algo repentino",
}

PREGUNTAS = {
    "ruta": {"instructions": "¿Le habla el usuario a Bob y qué quiere? Elige despedida si "
                             "quiere que Bob se vaya o termine. Elige ajeno solo si está claro "
                             "que no le habla a Bob. Ante la duda, elige charla.",
             "criteria": RUTAS},
    "emocion": {"instructions": "¿Qué emoción debe mostrar Bob al responder?",
                "criteria": EMOCIONES},
}


@dataclass
class Decision:
    ruta: Optional[str]          # None = usar los parsers actuales
    emocion: str                 # EMOCION_RESPALDO si JEV no dio una válida
    confianza_ruta: float
    confianza_emocion: float
    latencia_s: float
    respaldo: bool               # True si alguna de las dos partes usó respaldo
    error: str = ""


_cliente: Optional[TypeSafeClient] = None
_cliente_lock = threading.Lock()


def _get_cliente() -> TypeSafeClient:
    """Cliente único del SDK, sin reintentos y con el timeout del módulo."""
    global _cliente
    with _cliente_lock:
        if _cliente is None:
            _cliente = TypeSafeClient(api_key=TYPESAFE_API_KEY, model=JEV_MODEL,
                                      retry=RetryPolicy(max_retries=0),
                                      timeout=JEV_TIMEOUT_S)
        return _cliente


def _consultar(clave: str, frase: str, ultimo_turno: str) -> tuple:
    """Una pregunta a JEV. Devuelve (elección, confianza). Puede lanzar excepciones del SDK."""
    estado = {"message": frase}
    if ultimo_turno:
        estado["last_bob_turn"] = ultimo_turno
    pregunta = PREGUNTAS[clave]
    resultado = _get_cliente().system_one(
        model=JEV_MODEL,
        state=estado,
        questions={clave: Choice(instructions=pregunta["instructions"],
                                 criteria=pregunta["criteria"])},
    )
    respuesta = resultado.answers[clave]
    return respuesta.choice, float(respuesta.confidence)


def _decidir(frase: str, ultimo_turno: str = "") -> Decision:
    """Decide ruta y emoción para el turno. Nunca lanza excepciones."""
    if not TYPESAFE_API_KEY:
        return Decision(None, EMOCION_RESPALDO, 0.0, 0.0, 0.0, True, "falta TYPESAFE_API_KEY")

    t0 = time.monotonic()
    ejecutor = ThreadPoolExecutor(max_workers=2)
    try:
        futuros = {clave: ejecutor.submit(_consultar, clave, frase, ultimo_turno)
                   for clave in PREGUNTAS}
        # Plazo total compartido: pasado JEV_TIMEOUT_S, lo que no llegó es respaldo.
        terminados, _ = wait(futuros.values(), timeout=JEV_TIMEOUT_S)
    finally:
        ejecutor.shutdown(wait=False)

    errores, resultados = [], {}
    for clave, futuro in futuros.items():
        if futuro not in terminados:
            errores.append(f"{clave}: plazo agotado")
            continue
        try:
            resultados[clave] = futuro.result()
        except Exception as e:   # errores del SDK (TypeSafeError y subclases), red, etc.
            errores.append(f"{clave}: {type(e).__name__}: {e}")
    latencia = time.monotonic() - t0

    ruta, conf_ruta = resultados.get("ruta", (None, 0.0))
    emocion, conf_emocion = resultados.get("emocion", (None, 0.0))
    if "ruta" in resultados and ruta not in RUTAS:
        errores.append(f"ruta inválida: {ruta!r}")
        ruta, conf_ruta = None, 0.0
    if "emocion" in resultados and emocion not in EMOCIONES:
        errores.append(f"emoción inválida: {emocion!r}")
        emocion = None
    if emocion is None:
        emocion, conf_emocion = EMOCION_RESPALDO, 0.0

    return Decision(ruta=ruta, emocion=emocion, confianza_ruta=conf_ruta,
                    confianza_emocion=conf_emocion, latencia_s=latencia,
                    respaldo=bool(errores), error="; ".join(errores))


def registrar(d: Decision, ruta_archivo: Optional[str] = None) -> None:
    """J4: agrega la decisión como una línea JSON. No guarda el texto de la frase
    (lo que dice la gente). Nunca lanza excepciones: un fallo de disco no corta el turno."""
    linea = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "ruta": d.ruta,
        "emocion": d.emocion,
        "latencia_s": round(d.latencia_s, 3),
        "respaldo": d.respaldo,
        "confianza_ruta": d.confianza_ruta,
        "confianza_emocion": d.confianza_emocion,
        "error": d.error,
    }
    destino = ruta_archivo or LOG_DECISIONES
    try:
        with _registro_lock:
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            with open(destino, "a", encoding="utf-8") as f:
                f.write(json.dumps(linea, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[jev] no pude registrar la decisión en {destino}: {e}", flush=True)


def decidir(frase: str, ultimo_turno: str = "") -> Decision:
    """Decide ruta y emoción para el turno y lo registra (J4). Nunca lanza excepciones."""
    d = _decidir(frase, ultimo_turno)
    registrar(d)
    return d
