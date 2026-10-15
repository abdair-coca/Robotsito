"""
jev_decision.py — Decisiones de Bob con el modelo JEV (solo texto).

Dos peticiones en paralelo, cada una con una sola pregunta:
  - ruta: musica | recordatorio | asistente | charla (None si JEV no respondió)
  - emocion: una de EMOCIONES para la respuesta (CURIOSO si no respondió)

Medido en J1: una petición con las dos preguntas tarda ~1.0 s (mediana); cada
pregunta por separado en paralelo tarda ~0.5 s. Por eso van separadas.

Si JEV no responde en JEV_TIMEOUT_S, falla, responde algo inválido o falta la
clave, devuelve el respaldo para la parte que falló. Nunca lanza excepciones
hacia el pipeline.
"""
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Optional

from config import JEV_MODEL, JEV_TIMEOUT_S, TYPESAFE_API_KEY

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
EMOCION_RESPALDO = "CURIOSO"

RUTAS = {
    "musica": "Pide reproducir, pausar, cambiar o controlar música",
    "recordatorio": "Pide un recordatorio, temporizador o alarma",
    "asistente": "Pregunta por hora, fecha, clima o noticias",
    "charla": "Cualquier otra cosa: conversar, preguntar, pedir un chiste",
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
    "ruta": {"instructions": "¿Qué quiere el usuario?", "criteria": RUTAS},
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


def _post(cuerpo: dict) -> dict:
    """Llamada HTTP a JEV. Separada para poder simularla en pruebas."""
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(cuerpo).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {TYPESAFE_API_KEY}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=JEV_TIMEOUT_S) as r:
        return json.loads(r.read().decode("utf-8"))


def _cuerpo(frase: str, ultimo_turno: str, clave: str) -> dict:
    estado = {"message": frase}
    if ultimo_turno:
        estado["last_bob_turn"] = ultimo_turno
    return {"model": JEV_MODEL, "state": estado,
            "questions": {clave: {"type": "choice", **PREGUNTAS[clave]}}}


def _eleccion(datos: Optional[dict], clave: str, opciones: dict):
    """(elección, confianza) si la respuesta es válida; (None, 0.0) si no."""
    d = ((datos or {}).get("answers") or {}).get(clave) or {}
    elegida = d.get("choice")
    if elegida not in opciones:
        return None, 0.0
    return elegida, float(d.get("confidence", 0.0))


def decidir(frase: str, ultimo_turno: str = "") -> Decision:
    """Decide ruta y emoción para el turno. Nunca lanza excepciones."""
    if not TYPESAFE_API_KEY:
        return Decision(None, EMOCION_RESPALDO, 0.0, 0.0, 0.0, True, "falta TYPESAFE_API_KEY")

    t0 = time.monotonic()
    ejecutor = ThreadPoolExecutor(max_workers=2)
    try:
        futuros = {clave: ejecutor.submit(_post, _cuerpo(frase, ultimo_turno, clave))
                   for clave in PREGUNTAS}
        # Plazo total compartido: pasado JEV_TIMEOUT_S, lo que no llegó es respaldo.
        terminados, _ = wait(futuros.values(), timeout=JEV_TIMEOUT_S)
    finally:
        ejecutor.shutdown(wait=False)

    errores, datos = [], {}
    for clave, futuro in futuros.items():
        if futuro not in terminados:
            errores.append(f"{clave}: plazo agotado")
            continue
        try:
            datos[clave] = futuro.result()
        except (OSError, ValueError) as e:   # OSError: URLError, HTTPError, timeouts; ValueError: JSON
            errores.append(f"{clave}: {type(e).__name__}: {e}")
    latencia = time.monotonic() - t0

    ruta, conf_ruta = _eleccion(datos.get("ruta"), "ruta", RUTAS)
    emocion, conf_emocion = _eleccion(datos.get("emocion"), "emocion", EMOCIONES)
    if ruta is None and "ruta" in datos:
        errores.append(f"ruta inválida: {datos['ruta']!r}")
    if emocion is None:
        emocion = EMOCION_RESPALDO
        if "emocion" in datos:
            errores.append("emoción inválida")

    return Decision(ruta=ruta, emocion=emocion, confianza_ruta=conf_ruta,
                    confianza_emocion=conf_emocion, latencia_s=latencia,
                    respaldo=bool(errores), error="; ".join(errores))
