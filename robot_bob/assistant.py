"""
assistant.py — P9 Asistente Personal de Bob (info inmediata: hora / fecha / clima / noticias).

Enfoque: DETECCIÓN DE INTENCIÓN + INYECCIÓN al system prompt. NO usa tool-calling
nativo del LLM. Por qué:
  - Funciona con los 3 backends (Gemini, Groq y el LLM LOCAL chico llama3.2:3b,
    que no obedece tool-calling de forma fiable).
  - Respeta el pipeline de TTS por frase de voice_pipeline (no necesita una vuelta
    extra no-streaming para resolver la tool).
  - Mismo patrón que el resto del código (ver _intent_giro en voice_pipeline).

voice_pipeline llama a `contexto_asistente(texto)` por turno. Si el usuario pregunta
por hora/fecha/clima, devuelve un bloque de DATOS FRESCOS para concatenar al system
prompt; el LLM lo redacta con la personalidad de Bob (no leemos datos crudos en voz
alta). Si no hay intención de asistente, devuelve '' y no cambia nada.

Sin dependencias nuevas: datetime + urllib + xml.etree (stdlib). El clima usa
wttr.in y las noticias el RSS de Google News; ninguno pide API key (cero setup
para la feria).
"""

from __future__ import annotations

import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Optional

from config import CLIMA_ENABLED, CLIMA_CIUDAD, NOTICIAS_ENABLED, NOTICIAS_RSS_URL, NOTICIAS_MAX

# ── Nombres en español (locale de Windows poco fiable → hardcode) ───────────────
_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ── Detección de intención (keywords) ───────────────────────────────────────────
_KW_HORA = ("qué hora", "que hora", "la hora", "hora es", "hora son", "horas son")
_KW_FECHA = ("qué día", "que dia", "qué fecha", "que fecha", "fecha es", "fecha de hoy",
             "día es hoy", "dia es hoy", "día de hoy", "dia de hoy", "qué mes", "que mes",
             "en qué día", "en que dia", "qué año", "que año", "día estamos", "dia estamos")
# "tiempo" es ambiguo (hora vs clima); se exige contexto de clima explícito.
_KW_CLIMA = ("clima", "temperatura", "qué tiempo hace", "que tiempo hace", "el tiempo hace",
             "hace frío", "hace frio", "hace calor", "va a llover", "está lloviendo",
             "esta lloviendo", "lluvia", "pronóstico", "pronostico", "grados hace",
             "cuántos grados", "cuantos grados")
_KW_NOTICIAS = ("noticias", "noticia", "titulares", "qué pasa en el mundo",
                "que pasa en el mundo", "qué está pasando", "que esta pasando",
                "últimas noticias", "ultimas noticias", "qué hay de nuevo",
                "que hay de nuevo", "novedades")


def _quiere_hora(t: str) -> bool:
    return any(k in t for k in _KW_HORA)

def _quiere_fecha(t: str) -> bool:
    return any(k in t for k in _KW_FECHA)

def _quiere_clima(t: str) -> bool:
    return any(k in t for k in _KW_CLIMA)

def _quiere_noticias(t: str) -> bool:
    return any(k in t for k in _KW_NOTICIAS)


# ── Hora / fecha ────────────────────────────────────────────────────────────────
def hora_actual(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return now.strftime("%H:%M")

def fecha_actual(now: Optional[datetime] = None) -> str:
    now = now or datetime.now()
    return f"{_DIAS[now.weekday()]} {now.day} de {_MESES[now.month - 1]} de {now.year}"


# ── Clima (wttr.in, sin API key, con caché) ─────────────────────────────────────
_clima_cache: dict = {}        # ciudad -> (timestamp_monotonic, texto)
_CLIMA_TTL_S = 600             # 10 min: el clima no cambia tan rápido y evita spam

def obtener_clima(ciudad: Optional[str] = None) -> Optional[str]:
    """
    Devuelve un texto corto del clima ('Parcialmente nublado, +12°C') o None si
    falla la red. Cachea por ciudad ~10 min. Timeout corto para no colgar el turno.
    """
    if not CLIMA_ENABLED:
        return None
    ciudad = ciudad or CLIMA_CIUDAD
    ahora = time.monotonic()
    hit = _clima_cache.get(ciudad)
    if hit and ahora - hit[0] < _CLIMA_TTL_S:
        return hit[1]
    # %C condición, %t temperatura, &m métrico, &lang=es. wttr.in devuelve texto
    # plano con el parámetro format (sin HTML). UA tipo curl por las dudas.
    url = f"https://wttr.in/{urllib.request.quote(ciudad)}?format=%C+%t&lang=es&m"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=3.0) as r:
            texto = r.read().decode("utf-8", "replace").strip()
        if not texto or "Unknown location" in texto or "Sorry" in texto:
            return None
        texto = texto.replace("+", "").strip()      # "+12°C" → "12°C"
        _clima_cache[ciudad] = (ahora, texto)
        return texto
    except Exception:
        return None


# ── Noticias (Google News RSS, sin API key, con caché) ──────────────────────────
_noticias_cache: dict = {}     # url -> (timestamp_monotonic, [titulares])
_NOTICIAS_TTL_S = 600          # 10 min: evita re-fetch en cada turno

def obtener_noticias(n: Optional[int] = None) -> Optional[List[str]]:
    """
    Devuelve una lista de titulares (str) del RSS de Google News, o None si falla
    la red / parseo. Cachea ~10 min. Timeout corto para no colgar el turno.
    """
    if not NOTICIAS_ENABLED:
        return None
    n = n or NOTICIAS_MAX
    ahora = time.monotonic()
    hit = _noticias_cache.get(NOTICIAS_RSS_URL)
    if hit and ahora - hit[0] < _NOTICIAS_TTL_S:
        return hit[1][:n]
    try:
        req = urllib.request.Request(NOTICIAS_RSS_URL, headers={"User-Agent": "curl/8.0"})
        with urllib.request.urlopen(req, timeout=4.0) as r:
            raw = r.read()
        root = ET.fromstring(raw)
        # RSS 2.0: rss > channel > item > title. Google News antepone " - Fuente"
        # al título; lo cortamos para dejar solo el titular.
        titulares = []
        for item in root.iterfind(".//item/title"):
            t = (item.text or "").strip()
            if not t:
                continue
            titulares.append(t.rsplit(" - ", 1)[0].strip())
        if not titulares:
            return None
        _noticias_cache[NOTICIAS_RSS_URL] = (ahora, titulares)
        return titulares[:n]
    except Exception:
        return None


# ── API pública ─────────────────────────────────────────────────────────────────
def contexto_asistente(texto: str, now: Optional[datetime] = None) -> str:
    """
    Si `texto` pide hora/fecha/clima, devuelve un bloque para concatenar al system
    prompt con los datos en vivo. Si no, devuelve '' (no inyecta nada).
    """
    t = texto.lower()
    quiere_hora = _quiere_hora(t)
    quiere_fecha = _quiere_fecha(t)
    quiere_clima = _quiere_clima(t)
    quiere_noticias = _quiere_noticias(t)
    if not (quiere_hora or quiere_fecha or quiere_clima or quiere_noticias):
        return ""

    lineas = []
    if quiere_hora:
        lineas.append(f"- Hora actual: {hora_actual(now)}")
    if quiere_fecha:
        lineas.append(f"- Fecha de hoy: {fecha_actual(now)}")
    if quiere_clima:
        clima = obtener_clima()
        if clima:
            lineas.append(f"- Clima en {CLIMA_CIUDAD}: {clima}")
        else:
            lineas.append(f"- Clima en {CLIMA_CIUDAD}: no disponible ahora (sin "
                          "conexión); admití que no lo pudiste consultar.")
    if quiere_noticias:
        noticias = obtener_noticias()
        if noticias:
            lineas.append("- Titulares de hoy:")
            lineas.extend(f"    • {titular}" for titular in noticias)
        else:
            lineas.append("- Noticias: no disponibles ahora (sin conexión); admití "
                          "que no las pudiste consultar.")
    if not lineas:
        return ""

    return ("\n\n═══ DATOS EN VIVO (asistente) ═══\n"
            "El usuario preguntó por información en tiempo real. Responde con TU "
            "personalidad usando estos datos (no los leas crudos ni inventes otros). "
            "Contesta solo lo que preguntó:\n" + "\n".join(lineas))
