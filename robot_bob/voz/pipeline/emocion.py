"""Emoción del turno: tags [EMO:X] y prosodia."""
from __future__ import annotations

import re
from typing import Optional


# ── Tags de emoción del LLM (InteractiveGoal Fase A) ───────────────────────────
# El LLM prefija cada frase con [EMO:X]. Lo extraemos para el OLED y lo
# quitamos del texto antes del TTS (no debe leerse en voz alta).
# Acepta también "¡EMO:FELIZ!" sin corchetes: el LLM a veces lo escribe así y,
# si no se quita, el TTS lo lee en voz alta.
_EMO_TAG_RE = re.compile(r'¡?\[?EMO:([A-ZÁÉÍÓÚÜÑ_]+)\]?!?', re.IGNORECASE)


# Debe coincidir con los estados que el firmware (oled_ojos.py ESTADOS) sabe
# dibujar. Si un tag no está aquí, _extract_emo_tag lo descarta y el OLED cae al
# fallback por keyword (la emoción no se muestra).
_VALID_EMOS = {'FELIZ', 'MUY_FELIZ', 'EMOCIONADO', 'CURIOSO', 'TRAVIESO',
               'PENSANDO', 'SORPRENDIDO', 'ASUSTADO', 'CONFUNDIDO', 'AVERGONZADO',
               'TRISTE', 'MUY_TRISTE', 'ENOJADO', 'SOSPECHANDO', 'ORGULLOSO',
               'AMOR', 'HABLANDO'}


def _extract_emo_tag(text: str) -> tuple:
    """
    Devuelve (emo | None, texto_limpio).
    Usa el primer tag válido encontrado; borra TODOS los tags del texto.
    """
    emo = None
    for m in _EMO_TAG_RE.finditer(text):
        cand = m.group(1).upper()
        if emo is None and cand in _VALID_EMOS:
            emo = cand
    clean = _EMO_TAG_RE.sub('', text).strip()
    return emo, clean


def _marcar_emo(sent: str, emo: str) -> str:
    """
    J3: la emoción la decide JEV, no el LLM. Quita los tags que el LLM escriba
    y antepone el de JEV, para que el OLED y el TTS lo lean como antes.
    """
    return f'[EMO:{emo}] ' + _EMO_TAG_RE.sub('', sent).strip()


# Prosodia por emoción (config local, gitignoreado). Fallback neutro si falta.
try:
    from config import TTS_RATE_BASE, TTS_PITCH_BASE, TTS_EMO_PROSODY
except Exception:
    TTS_RATE_BASE, TTS_PITCH_BASE, TTS_EMO_PROSODY = "+0%", "+0Hz", {}


def _prosody_for_emo(emo: Optional[str]) -> tuple:
    """Devuelve (rate, pitch) edge-tts según la emoción; default = base."""
    if emo:
        p = TTS_EMO_PROSODY.get(emo.upper())
        if p:
            return p
    return (TTS_RATE_BASE, TTS_PITCH_BASE)
