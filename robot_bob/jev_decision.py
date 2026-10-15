"""
jev_decision.py — Decisiones de Bob con el modelo JEV (solo texto), vía typesafe_sdk.

Dos peticiones en paralelo, cada una con una sola pregunta:
  - ruta: musica | recordatorio | asistente | charla | ajeno (None si JEV no respondió)
  - emocion: una de EMOCIONES para la respuesta (CURIOSO si no respondió)

Medido en J1: una petición con las dos preguntas tarda ~1.0 s (mediana); cada
pregunta por separado en paralelo tarda ~0.5 s. Por eso van separadas.

El SDK no reintenta (RetryPolicy(max_retries=0)) para que el plazo de
JEV_TIMEOUT_S sea real. Si JEV no responde a tiempo, falla o responde algo
inválido, devuelve el respaldo para la parte que falló. Nunca lanza excepciones
hacia el pipeline.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Optional

from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

from config import JEV_MODEL, JEV_TIMEOUT_S, TYPESAFE_API_KEY

EMOCION_RESPALDO = "CURIOSO"

RUTAS = {
    "musica": "Pide reproducir, pausar, cambiar o controlar música",
    "recordatorio": "Pide un recordatorio, temporizador o alarma",
    "asistente": "Pregunta por hora, fecha, clima o noticias",
    "charla": "Cualquier otra cosa: conversar, preguntar, pedir un chiste",
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
    "ruta": {"instructions": "¿Le habla el usuario a Bob y qué quiere? Elige ajeno solo "
                             "si está claro que no le habla a Bob; ante la duda, elige charla.",
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


def decidir(frase: str, ultimo_turno: str = "") -> Decision:
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
