"""Reglas de turno: salida, despedida, giros, nombres, promesas y alucinaciones de Whisper."""
from __future__ import annotations

import re
from typing import Optional
from config import EXIT_PHRASES, GOODBYE_PHRASES
from voz import stt
from voz.pipeline.consola import console
from voz.pipeline.frases import FRASE_SIN_ACCION
import errores


# ── E-C: lo que el LLM de la charla no puede afirmar ───────────────────────────
# En la charla no hay herramientas: si Bob dice "voy a crear una carpeta", nadie la crea.
_RE_HERRAMIENTA = re.compile(
    r'\b(carpeta|archivo|spotify|playlist|canci[oó]n|m[uú]sica|modo ingenier\w*|firmware|'
    r'servos?|navegador|correo|mensaje|contrase\w+|descarg\w*)\b', re.IGNORECASE)


_VERBO_HERRAMIENTA = (r'(?:crear|abrir|poner|reproducir|enviar|borrar|descargar|guardar|buscar|'
                      r'instalar|cambiar|encender|apagar|configurar|recalibrar|afinar)')


_RE_ACCION_PROPIA = re.compile(
    rf"\b(?:(?:voy|vamos) a {_VERBO_HERRAMIENTA}|acabo de \w+|a punto de \w+|"
    r"puedo (?:reproducir|poner|abrir|crear|enviar|borrar|descargar)|"
    r"pongo|abro|reproduzco|puse|abr[ií]|recalibr\w*|afin\w*|"
    r"estoy (?:creando|abriendo|poniendo|reproduciendo|buscando|enviando|recalibrando|afinando))\b",
    re.IGNORECASE)


_RE_NEGACION = re.compile(r'\bno (?:lo |la |te )?(?:puedo|voy|pude|podr\w*)\b', re.IGNORECASE)


def _promete_accion(frase: str) -> bool:
    """¿La frase afirma una acción de herramienta? Una negación ("no puedo reproducir")
    no cuenta: es justo lo que debe decir Bob."""
    if _RE_NEGACION.search(frase):
        return False
    return bool(_RE_HERRAMIENTA.search(frase) and _RE_ACCION_PROPIA.search(frase))


def _sin_promesas(frase: str) -> str:
    if _promete_accion(frase):
        errores.aviso('GUARDIA', 'Bob iba a decir que hizo algo que no hizo; cambié la frase.', frase)
        return FRASE_SIN_ACCION
    return frase


# Comandos de voz para que Bob gire buscando al que habla.
_GIRO_DER_KW = ('a la derecha', 'tu derecha', 'a tu derecha', 'por la derecha',
                'hacia la derecha')


_GIRO_IZQ_KW = ('a la izquierda', 'tu izquierda', 'a tu izquierda', 'por la izquierda',
                'hacia la izquierda')


_GIRO_BUSCAR_KW = ('date la vuelta', 'date vuelta', 'da la vuelta', 'date media vuelta',
                   'date la media vuelta', 'voltea', 'volteate', 'voltéate', 'gira',
                   'girate', 'gírate', 'date la vueltita', 'estoy detras', 'estoy detrás',
                   'detras de ti', 'detrás de ti', 'atras de ti', 'atrás de ti',
                   'aca atras', 'acá atrás', 'aqui atras', 'aquí atrás', 'mira atras',
                   'mira atrás', 'date la vue')


# J5: ruta de JEV → valor de scan_request (lo que lee behavior.py)
_GIRO_JEV = {'giro_derecha': 'derecha', 'giro_izquierda': 'izquierda', 'giro_buscar': 'buscar'}


def _intent_giro(texto: str):
    """Devuelve 'derecha' | 'izquierda' | 'buscar' | None según el comando de voz."""
    t = texto.lower()
    if any(k in t for k in _GIRO_DER_KW):
        return 'derecha'
    if any(k in t for k in _GIRO_IZQ_KW):
        return 'izquierda'
    if any(k in t for k in _GIRO_BUSCAR_KW):
        return 'buscar'
    return None


def _giro_turno(texto: str, ruta: Optional[str]):
    """J5: el giro lo decide JEV. Si JEV no respondió (None), palabras clave de siempre.
    Si JEV respondió otra ruta, no es un giro."""
    if ruta is None:
        return _intent_giro(texto)
    return _GIRO_JEV.get(ruta)


# Extracción del nombre cuando el usuario se presenta.
_RE_NOMBRE = re.compile(
    r'\b(?:me llamo|mi nombre es|me dicen|soy)\s+([a-záéíóúñ]{2,20})', re.IGNORECASE)


_NO_NOMBRE = {'de', 'un', 'una', 'el', 'la', 'muy', 'yo', 'estudiante', 'ingeniero',
              'ingeniera', 'profe', 'profesor', 'doctor', 'el', 'tu', 'su', 'que',
              'bien', 'mal', 'feliz', 'triste', 'de', 'del', 'para', 'medio'}


def _extraer_nombre(texto: str):
    """Devuelve un nombre si el usuario se presentó ('me llamo X'), o None."""
    m = _RE_NOMBRE.search(texto)
    if not m:
        return None
    n = m.group(1).strip()
    if n.lower() in _NO_NOMBRE:
        return None
    return n.capitalize()


# Enrolamiento explícito de la voz del dueño ("Bob, aprende mi voz").
_RE_APRENDER_VOZ = re.compile(
    r'\b(?:apr[eé]nde(?:te)?|memoriza|gu[aá]rdate|guarda)\s+(?:bien\s+)?mi\s+voz\b',
    re.IGNORECASE)


def _pide_aprender_voz(texto: str) -> bool:
    return bool(_RE_APRENDER_VOZ.search(texto or ''))


def _is_exit(text: str) -> bool:
    return text.lower().strip(' .,!?¿¡') in EXIT_PHRASES


def _is_goodbye(text: str) -> bool:
    return text.lower().strip(' .,!?¿¡') in GOODBYE_PHRASES


def _es_alucinacion(texto: str) -> bool:
    """En reposo: frases que Whisper inventa con silencio o ruido (stt.ALUCINACIONES)."""
    t = ' '.join(stt.palabras(texto))
    if not t:
        return True
    if 'amara' in t or 'subtitul' in t or 'suscrib' in t:
        return True
    return t in stt.ALUCINACIONES
