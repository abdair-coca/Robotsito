"""
puente_cli.py — Puente entre Bob y su CLI (bob-cli/, Ink).

El CLI lanza a Bob con BOB_CLI=1. Con eso, todo lo que Bob escribe sale por stdout
como NDJSON, un evento por línea:

  {"t": "log", "origen": "bob" | "stderr", "nivel": "info" | "aviso" | "error",
   "ansi": "<la línea, con los colores de Rich>", "turno": "usuario" | "bob" (opcional)}
  {"t": "estado", ...}   lo que cambió del estado de Bob (ver estado_cli.py)

y por stdin llegan comandos {"cmd": "<nombre>", ...} que se despachan a lo registrado
con al_comando(). Sin BOB_CLI, activar() no hace nada: Bob escribe en la consola como
siempre.

No hace falta tocar los mensajes: las consolas de Rich y print() buscan sys.stdout al
escribir, y acá sys.stdout y sys.stderr pasan a ser escritores de eventos.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import threading
from typing import Callable, Dict, Optional

ACTIVO = False

_canal = None                    # stdout binario real: el canal NDJSON hacia el CLI
_canal_lock = threading.Lock()
_local = threading.local()       # por hilo: línea a medio escribir y nivel del mensaje
_comandos: Dict[str, Callable[[dict], None]] = {}


def activar() -> bool:
    """Con BOB_CLI=1, redirige stdout/stderr a eventos y empieza a leer comandos.
    Llamar al arrancar, antes de crear consolas o imprimir. True si quedó activo."""
    global ACTIVO, _canal
    if ACTIVO or os.environ.get('BOB_CLI') != '1':
        return ACTIVO
    # Con stdout por pipe, Rich en Windows cae a 16 colores ("legacy windows") aunque
    # se pida truecolor: el naranja de los avisos saldría amarillo.
    import rich.console
    rich.console.detect_legacy_windows = lambda: False
    entrada = _apartar_stdin()
    _canal = sys.stdout.buffer
    sys.stdout = _Escritor('bob')
    sys.stderr = _Escritor('stderr')
    ACTIVO = True
    threading.Thread(target=_leer_comandos, args=(entrada,), daemon=True,
                     name='cli-comandos').start()
    return True


def _apartar_stdin():
    """Devuelve una copia de stdin para leer los comandos y deja el stdin estándar
    (fd 0) apuntando a NUL.

    En Windows, un hilo bloqueado leyendo el pipe de stdin traba a cualquier otro que
    consulte ese mismo handle: el runtime de Fortran de scipy lo hace al cargar su DLL,
    y `import main` quedaba colgado. Con la copia, lo que mire el stdin estándar ve NUL.
    En modo CLI nadie más lee stdin (input() da EOF, como debe: el nombre de la E
    llega por comando)."""
    copia = os.dup(sys.stdin.fileno())
    nul = os.open(os.devnull, os.O_RDONLY)
    os.dup2(nul, 0)              # en consola, también cambia el handle estándar
    os.close(nul)
    return open(copia, 'r', encoding='utf-8', closefd=True)


def emitir(evento: dict) -> None:
    """Manda un evento al CLI. Sin CLI no hace nada; nunca lanza."""
    if not ACTIVO:
        return
    linea = (json.dumps(evento, ensure_ascii=False) + '\n').encode('utf-8')
    with _canal_lock:
        try:
            _canal.write(linea)
            _canal.flush()
        except (OSError, ValueError):
            pass                 # el CLI se cerró: no hay a quién mostrarle nada


@contextlib.contextmanager
def nivel(n: str):
    """Marca con nivel `n` ("aviso", "error") las líneas que se escriban adentro."""
    previo = getattr(_local, 'nivel', 'info')
    _local.nivel = n
    try:
        yield
    finally:
        _local.nivel = previo


@contextlib.contextmanager
def turno(quien: str):
    """Marca las líneas escritas adentro como turno de `quien` ("usuario", "bob"): el
    CLI las resalta. El texto no cambia; sin CLI no hace nada."""
    previo = getattr(_local, 'turno', None)
    _local.turno = quien
    try:
        yield
    finally:
        _local.turno = previo


def al_comando(nombre: str, fn: Callable[[dict], None]) -> None:
    """Registra qué hacer cuando el CLI manda {"cmd": nombre}. fn recibe el comando."""
    _comandos[nombre] = fn


class _Escritor:
    """Reemplazo de sys.stdout/sys.stderr: cada línea completa es un evento "log".
    Cada hilo arma su propia línea, así dos hilos que imprimen a la vez no se mezclan."""

    encoding = 'utf-8'
    errors = 'strict'

    def __init__(self, origen: str):
        self.origen = origen

    def write(self, texto: str) -> int:
        clave = 'linea_' + self.origen
        pendiente = getattr(_local, clave, '') + texto
        *lineas, resto = pendiente.split('\n')
        setattr(_local, clave, resto)
        quien = getattr(_local, 'turno', None)
        for linea in lineas:
            evento = {'t': 'log', 'origen': self.origen,
                      'nivel': getattr(_local, 'nivel', 'info'), 'ansi': linea.rstrip('\r')}
            if quien:
                evento['turno'] = quien
            emitir(evento)
        return len(texto)

    def flush(self) -> None:
        pass                     # solo salen líneas completas

    def isatty(self) -> bool:
        return True              # Rich escribe con colores (el CLI los muestra)

    def writable(self) -> bool:
        return True

    def fileno(self) -> int:
        # No hay descriptor propio; quien lo pide (faulthandler, subprocess) espera esto.
        raise io.UnsupportedOperation('fileno')


def _avisar(texto: str) -> None:
    emitir({'t': 'log', 'origen': 'bob', 'nivel': 'aviso', 'ansi': f'[cli] {texto}'})


def _leer_comandos(entrada) -> None:
    _despachar(entrada)
    # stdin cerrado: el CLI ya no está (se cerró o se cayó). Bob no debe quedar
    # corriendo sin que nadie lo vea y con COM3 tomado: cierra como con "salir" o,
    # si no hay quien lo atienda, como con Ctrl+C (corre el finally de main).
    # raise_signal y no _thread.interrupt_main: en Windows solo la señal despierta
    # un time.sleep largo del hilo principal.
    salir = _comandos.get('salir')
    if salir is not None:
        salir({'cmd': 'salir'})
    else:
        import signal
        signal.raise_signal(signal.SIGINT)


def _despachar(entrada) -> None:
    for linea in entrada:
        linea = linea.strip()
        if not linea:
            continue
        try:
            comando = json.loads(linea)
            nombre = comando['cmd']
        except (ValueError, TypeError, KeyError):
            _avisar(f'comando ilegible: {linea[:80]}')
            continue
        fn: Optional[Callable[[dict], None]] = _comandos.get(nombre)
        if fn is None:
            _avisar(f'comando desconocido: {nombre}')
            continue
        try:
            fn(comando)
        except Exception as e:
            _avisar(f'el comando {nombre} falló: {e}')
