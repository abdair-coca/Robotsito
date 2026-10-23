"""
errores.py — Mensajes de error de la consola, con el mismo formato en todo Bob:

  ✖ CÁMARA     No llega video. Revisa que la ESP32-CAM esté encendida. (timed out)
  ⚠ CLAUDE     No respondió a tiempo: este turno lo contesta Groq.

Parte, qué pasó y qué hace Bob (o qué hacer tú); el detalle técnico va al final, entre
paréntesis y atenuado.
  error(): rojo. Algo dejó de funcionar.
  aviso(): naranja. Bob se recupera solo o sigue con un respaldo.
formato_error() / formato_aviso() devuelven el texto (markup de Rich) para quien imprime
con su propia función de log.
"""
from rich.console import Console
from rich.markup import escape

_console = Console()
_ANCHO_PARTE = 10


def _simbolo(simbolo: str, alterno: str) -> str:
    """Una consola sin Unicode (cp1252) no puede escribir ✖ ni ⚠: ahí va la letra."""
    try:
        simbolo.encode(_console.encoding or 'utf-8')
        return simbolo
    except (UnicodeError, LookupError):
        return alterno


def _formato(simbolo: str, color: str, parte: str, texto: str, detalle=None) -> str:
    extra = f' [dim]({escape(str(detalle))})[/]' if detalle not in (None, '') else ''
    return f'[{color}]{simbolo} {parte:<{_ANCHO_PARTE}} {escape(texto)}[/]{extra}'


def formato_error(parte: str, texto: str, detalle=None) -> str:
    return _formato(_simbolo('✖', 'X'), 'bold red', parte, texto, detalle)


def formato_aviso(parte: str, texto: str, detalle=None) -> str:
    return _formato(_simbolo('⚠', '!'), 'dark_orange', parte, texto, detalle)


def _imprimir(markup: str) -> None:
    """Un mensaje de error nunca debe tumbar a Bob: si Rich falla, va en texto plano."""
    try:
        _console.print(markup, highlight=False)
    except Exception:
        from rich.text import Text
        try:
            print(Text.from_markup(markup).plain.encode('ascii', 'replace').decode('ascii'), flush=True)
        except Exception:
            pass


def error(parte: str, texto: str, detalle=None) -> None:
    _imprimir(formato_error(parte, texto, detalle))


def aviso(parte: str, texto: str, detalle=None) -> None:
    _imprimir(formato_aviso(parte, texto, detalle))
