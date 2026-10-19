"""Contexto que se inyecta al LLM por turno: asistente y parámetros de Groq."""
from __future__ import annotations

from typing import Optional, Dict
from asistente.assistant import contexto_asistente


def _contexto_asistente_turno(texto: str, ruta: Optional[str]) -> str:
    """J2: datos en vivo para el LLM según la ruta de JEV.
    None (JEV no respondió): palabras clave de siempre.
    'asistente' sin palabra clave: hora y fecha (locales, no dependen de red).
    Otra ruta: no se inyecta nada."""
    if ruta not in (None, 'asistente'):
        return ''
    ctx = contexto_asistente(texto)
    if ruta == 'asistente' and not ctx:
        # Las palabras clave de assistant.py son frases: "qué hora" y "qué fecha es".
        ctx = contexto_asistente('qué hora es y qué fecha es')
    return ctx


def _extra_groq(modelo: str) -> Dict:
    """Parámetros por modelo de Groq. GPT-OSS gasta max_tokens razonando: `low` deja
    lugar al texto. Qwen3 sin razonamiento responde en ~0.6 s (medido 2026-10-17)."""
    if modelo.startswith('openai/gpt-oss-'):
        return {'reasoning_effort': 'low'}
    if modelo.startswith('qwen/qwen3'):
        return {'reasoning_effort': 'none'}
    return {}
