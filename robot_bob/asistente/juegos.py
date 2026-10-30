"""
asistente/juegos.py — J1: adivinanzas y trivia por LLM.

Un turno de juego: Bob propone una pregunta, el dueño responde y Bob dice si acertó,
con el puntaje. El LLM entra como una función `llm(prompt) -> str`, así el test corre
sin red.

Todavía no está conectado a la charla: el disparador por voz es una decisión aparte.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Callable, Optional

_ARTICULOS = {'el', 'la', 'los', 'las', 'un', 'una'}
_CAMPO = re.compile(r'(?im)^\s*(PREGUNTA|RESPUESTA)\s*:\s*(.+?)\s*$')

_INSTRUCCION = {
    'adivinanza': 'Inventa una adivinanza corta en español. Responde solo con dos líneas:\n'
                  'PREGUNTA: <la adivinanza>\nRESPUESTA: <la respuesta, una o dos palabras>',
    'trivia': 'Inventa una pregunta de trivia corta y fácil en español. Responde solo con dos líneas:\n'
              'PREGUNTA: <la pregunta>\nRESPUESTA: <la respuesta, una o dos palabras>',
}


def _tokens(texto: str) -> list:
    """Minúsculas, sin tildes ni puntuación y sin artículos."""
    sin_tildes = ''.join(c for c in unicodedata.normalize('NFKD', texto.lower())
                         if not unicodedata.combining(c))
    return [t for t in re.findall(r'[a-z0-9]+', sin_tildes) if t not in _ARTICULOS]


@dataclass
class Pregunta:
    tipo: str
    texto: str
    respuesta: str


def _parsear(salida: str, tipo: str) -> Optional[Pregunta]:
    campos = {m.group(1).upper(): m.group(2) for m in _CAMPO.finditer(salida)}
    if 'PREGUNTA' not in campos or 'RESPUESTA' not in campos:
        return None
    return Pregunta(tipo, campos['PREGUNTA'], campos['RESPUESTA'])


class Juego:
    """Adivinanzas o trivia con su puntaje. Una pregunta a la vez."""

    def __init__(self, llm: Callable[[str], str]) -> None:
        self._llm = llm
        self.puntos = 0
        self.actual: Optional[Pregunta] = None

    def proponer(self, tipo: str = 'adivinanza') -> str:
        """Pide una pregunta al LLM y la deja como la actual. Devuelve lo que dice Bob."""
        if tipo not in _INSTRUCCION:
            raise ValueError(f'tipo desconocido: {tipo}')
        pregunta = _parsear(self._llm(_INSTRUCCION[tipo]), tipo)
        self.actual = pregunta
        if pregunta is None:
            return 'Se me olvidó la pregunta. ¿Probamos otra vez?'
        return pregunta.texto

    def responder(self, texto: str) -> str:
        """Compara la respuesta del dueño con la actual. Devuelve lo que dice Bob."""
        if self.actual is None:
            return 'Todavía no hay pregunta. Dime "adivinanza" o "trivia".'
        esperada = self.actual.respuesta
        tokens_esperada = _tokens(esperada)
        tokens_dueno = set(_tokens(texto))
        acerto = bool(tokens_esperada) and all(t in tokens_dueno for t in tokens_esperada)
        self.actual = None
        if acerto:
            self.puntos += 1
            return f'¡Correcto! Llevas {self.puntos} punto(s).'
        return f'No era. Era "{esperada}". Llevas {self.puntos} punto(s).'
