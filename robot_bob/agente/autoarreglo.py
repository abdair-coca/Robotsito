"""
autoarreglo.py — Bob arregla su propio código cuando un hilo se cae por un error (Fase S, S6).

Disparo (S-D10): una excepción no atrapada en un hilo (threading.excepthook), con su
traceback. Las partes que se rinden (audio, serial…) no disparan: casi siempre es físico.

Con el modo ingeniero activo (S-D2):
  1. Bob avisa (consola y voz, cuando está libre).
  2. Claude Code (Sonnet, S-D11) con Read, Glob, Grep, Edit y Write sobre robot_bob/ recibe
     el traceback y la bitácora de salud. Cada Edit y Write pasa por aprobar(): se niega en
     las zonas prohibidas (S-D7) y, antes de permitirlo, se guarda la copia del archivo
     (o se anota que era nuevo).
  3. Bob compila e importa los módulos editados y corre los tests que los importan, con los
     archivos originales y con los editados (S-D13). Si no compila o un test que pasaba
     ahora falla, revierte las copias y no reinicia.
  4. Si todo pasa: deja el informe (traceback, respuesta, diff, tests) en logs/arreglos/,
     deja la marca para el supervisor (que revierte si Bob se cae al arrancar) y pide
     reiniciarse (código 3, solo si corre con el supervisor).
Sin modo ingeniero: Claude solo lee y escribe el diagnóstico y el arreglo propuesto en
logs/arreglos/. No edita nada.

Frenos: uno a la vez; MAX_POR_DIA ediciones por día; el mismo error (tipo y lugar) se
intenta una sola vez por sesión.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Iterable, List, Optional

import errores

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELO = 'claude-sonnet-5-5'
MAX_POR_DIA = 3
TIMEOUT_CLAUDE_S = 600.0
MAX_HERRAMIENTAS = 60
TIMEOUT_TEST_S = 120.0
PARALELO = 4
LINEAS_BITACORA = 20

HERRAMIENTAS_EDICION = 'Read,Glob,Grep,Edit,Write'
HERRAMIENTAS_LECTURA = 'Read,Glob,Grep'
ACCIONES = ('Edit', 'Write')


def prohibido(ruta: str, raiz: str = RAIZ) -> Optional[str]:
    """Motivo por el que no se puede editar `ruta`, o None si se puede (S-D7)."""
    real = os.path.realpath(ruta)
    base = os.path.realpath(raiz)
    try:
        dentro = os.path.commonpath([real, base]) == base
    except ValueError:               # otra unidad de disco
        dentro = False
    if not dentro:
        return 'está fuera de robot_bob'
    rel = os.path.relpath(real, base).replace(os.sep, '/')
    nombre = os.path.basename(real)
    if nombre == '.env' or nombre.startswith('.env.'):
        return 'es un archivo de secretos'
    if nombre.startswith('memory.db'):
        return 'es la memoria de Bob'
    if rel in ('config.py', 'devices.json'):
        return 'es la configuración'
    return None


def firma(exc: BaseException, tb, raiz: str = RAIZ) -> str:
    """Tipo del error y el último lugar del código de Bob donde pasó."""
    lugar = ''
    for frame in traceback.extract_tb(tb):
        if os.path.realpath(frame.filename).startswith(os.path.realpath(raiz)):
            lugar = f'{os.path.relpath(frame.filename, raiz).replace(os.sep, "/")}:{frame.lineno}'
    return f'{type(exc).__name__}@{lugar or "?"}'


def modulo_de(ruta: str, raiz: str = RAIZ) -> Optional[str]:
    """voz/pipeline/__init__.py → voz.pipeline; nucleo/salud.py → nucleo.salud."""
    rel = os.path.relpath(ruta, raiz).replace(os.sep, '/')
    if not rel.endswith('.py'):
        return None
    partes = rel[:-3].split('/')
    if partes[-1] == '__init__':
        partes = partes[:-1]
    return '.'.join(partes) or None


def tests_relacionados(editados: Iterable[str], raiz: str = RAIZ) -> List[str]:
    """Tests de tests/unit que importan algún módulo editado (o que fueron editados)."""
    carpeta = os.path.join(raiz, 'tests', 'unit')
    if not os.path.isdir(carpeta):
        return []
    patrones = []
    elegidos = set()
    for ruta in editados:
        if os.path.dirname(os.path.realpath(ruta)) == os.path.realpath(carpeta):
            elegidos.add(os.path.realpath(ruta))
            continue
        mod = modulo_de(ruta, raiz)
        if not mod:
            continue
        paquete, _, hoja = mod.rpartition('.')
        patrones.append(re.compile(rf'\b{re.escape(mod)}\b'))
        patrones.append(re.compile(rf'^\s*import\s+{re.escape(hoja)}\b', re.M) if not paquete
                        else re.compile(rf'^\s*from\s+{re.escape(paquete)}\s+import\s.*\b{re.escape(hoja)}\b',
                                        re.M))
    for nombre in sorted(os.listdir(carpeta)):
        if not (nombre.startswith('test_') and nombre.endswith('.py')):
            continue
        ruta = os.path.join(carpeta, nombre)
        try:
            with open(ruta, encoding='utf-8') as f:
                texto = f.read()
        except OSError:
            continue
        if any(p.search(texto) for p in patrones):
            elegidos.add(os.path.realpath(ruta))
    return sorted(elegidos)


def borrar_bytecode(rutas: Iterable[str]) -> None:
    """Borra el .pyc de cada archivo. Un cambio del mismo tamaño en el mismo segundo no
    invalida el .pyc (Python compara fecha y tamaño), y el test correría el código viejo."""
    import importlib.util
    for ruta in rutas:
        if not ruta.endswith('.py'):
            continue
        try:
            os.remove(importlib.util.cache_from_source(ruta))
        except (OSError, ValueError, NotImplementedError):
            pass


# Con la salida capturada, Python escribe en cp1252 y un test que imprime "→" se cae
# (medido: 7 de 8 "fallas" de la suite eran eso). Los tests corren con UTF-8.
_ENTORNO_TESTS = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}


def correr_tests(tests: List[str], raiz: str = RAIZ) -> Dict[str, bool]:
    """Cada test es un script que sale con 0 si pasa. Un timeout cuenta como falla.
    Con -B: no deja .pyc que otra corrida pueda tomar por buenos (ver borrar_bytecode)."""
    def uno(ruta: str) -> bool:
        try:
            r = subprocess.run([sys.executable, '-B', ruta], cwd=raiz, capture_output=True,
                               env=_ENTORNO_TESTS,
                               timeout=TIMEOUT_TEST_S)
            return r.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False
    with ThreadPoolExecutor(max_workers=PARALELO) as pool:
        return dict(zip(tests, pool.map(uno, tests)))


def compila_e_importa(editados: Iterable[str], raiz: str = RAIZ) -> Optional[str]:
    """None si todos los .py editados compilan e importan; si no, el error."""
    for ruta in editados:
        if not ruta.endswith('.py') or not os.path.exists(ruta):
            continue
        try:                         # compile() y no py_compile: no escribe .pyc
            with open(ruta, 'rb') as f:
                compile(f.read(), ruta, 'exec')
        except SyntaxError as e:
            return f'no compila {os.path.relpath(ruta, raiz)}: línea {e.lineno}: {e.msg}'
        mod = modulo_de(ruta, raiz)
        if not mod or mod.startswith('tests.') or mod == 'main':
            continue
        try:
            r = subprocess.run([sys.executable, '-B', '-c', f'import {mod}'], cwd=raiz,
                               capture_output=True, text=True, encoding='utf-8',
                               errors='replace', env=_ENTORNO_TESTS, timeout=TIMEOUT_TEST_S)
        except subprocess.TimeoutExpired:
            return f'importar {mod} tardó más de {TIMEOUT_TEST_S:.0f} s'
        if r.returncode != 0:
            return f'no importa {mod}: {r.stderr.strip().splitlines()[-1:]}'
    return None


class Copias:
    """Lo que había antes de cada archivo que Claude edita: el contenido, o None si era nuevo."""

    def __init__(self) -> None:
        self.originales: Dict[str, Optional[bytes]] = {}

    def guardar(self, ruta: str) -> None:
        ruta = os.path.realpath(ruta)
        if ruta in self.originales:
            return
        self.originales[ruta] = open(ruta, 'rb').read() if os.path.exists(ruta) else None

    def actuales(self) -> Dict[str, Optional[bytes]]:
        return {r: (open(r, 'rb').read() if os.path.exists(r) else None) for r in self.originales}

    @staticmethod
    def poner(contenidos: Dict[str, Optional[bytes]]) -> None:
        for ruta, datos in contenidos.items():
            if datos is None:
                if os.path.exists(ruta):
                    os.remove(ruta)
            else:
                with open(ruta, 'wb') as f:
                    f.write(datos)

    def revertir(self) -> None:
        self.poner(self.originales)


def diff(originales: Dict[str, Optional[bytes]], actuales: Dict[str, Optional[bytes]],
         raiz: str = RAIZ) -> str:
    salida = []
    for ruta in sorted(originales):
        antes = (originales[ruta] or b'').decode('utf-8', 'replace').splitlines(keepends=True)
        despues = (actuales.get(ruta) or b'').decode('utf-8', 'replace').splitlines(keepends=True)
        rel = os.path.relpath(ruta, raiz).replace(os.sep, '/')
        salida += difflib.unified_diff(antes, despues, f'a/{rel}', f'b/{rel}')
    return ''.join(salida)


_PROMPT = (
    'Eres el mecánico del código de Bob, un robot humanoide en Python (carpeta {raiz}). '
    'Un hilo de Bob se cayó por un error que nadie atrapó. Busca la causa leyendo el código. '
    'Nunca leas ni cites .env ni claves. ')
_PROMPT_EDICION = (
    'Arréglala con el cambio mínimo, en el estilo del código que la rodea. No edites .env, '
    'memory.db, config.py ni devices.json. No ejecutes nada: Bob compila y corre los tests '
    'después. Si no estás seguro de la causa, no edites y explica por qué. Al terminar, '
    'responde en tres líneas: archivos cambiados, causa y arreglo.')
_PROMPT_LECTURA = (
    'No puedes editar: responde con la causa y el arreglo propuesto como diff unificado, '
    'para que el dueño lo revise.')


class Autoarreglo:
    def __init__(self, en_ingeniero: Callable[[], bool],
                 crear_sesion: Optional[Callable[..., object]] = None,
                 avisar: Callable[[str], None] = lambda evento: None,
                 pedir_reinicio: Callable[[], None] = lambda: None,
                 raiz: str = RAIZ, dir_arreglos: Optional[str] = None,
                 bitacora: Optional[str] = None,
                 correr: Callable[[List[str]], Dict[str, bool]] = None,
                 compilar: Callable[[Iterable[str]], Optional[str]] = None,
                 supervisado: Optional[bool] = None,
                 reloj: Callable[[], float] = time.time,
                 lanzar: Callable[[Callable[[], None]], None] = None) -> None:
        """avisar(evento): 'empiezo', 'listo', 'revertido', 'propuesta' (frases de Bob).
        pedir_reinicio(): cierra Bob para que el supervisor lo relance (código 3)."""
        self._en_ingeniero = en_ingeniero
        self._crear_sesion = crear_sesion or _sesion_claude
        self._avisar = avisar
        self._pedir_reinicio = pedir_reinicio
        self.raiz = raiz
        self.dir_arreglos = dir_arreglos or os.path.join(raiz, 'logs', 'arreglos')
        self._bitacora = bitacora or os.path.join(raiz, 'logs', 'salud.jsonl')
        self._correr = correr or (lambda tests: correr_tests(tests, raiz))
        self._compilar = compilar or (lambda rutas: compila_e_importa(rutas, raiz))
        self._supervisado = (os.environ.get('BOB_SUPERVISADO') == '1') if supervisado is None \
            else supervisado
        self._reloj = reloj
        self._lanzar = lanzar or (lambda f: threading.Thread(target=f, daemon=True,
                                                             name='autoarreglo').start())
        self._ocupado = threading.Lock()
        self._vistos: set = set()
        self.reinicio_pedido = False
        self._hook_previo = None

    # ── Disparo ────────────────────────────────────────────────────────────────
    def instalar(self) -> None:
        """Engancha threading.excepthook: el traceback se sigue imprimiendo como siempre."""
        self._hook_previo = threading.excepthook

        def hook(args) -> None:
            self._hook_previo(args)
            if args.exc_type is not SystemExit and args.exc_value is not None:
                nombre = args.thread.name if args.thread else '?'
                self.al_error(args.exc_value, args.exc_traceback, nombre)

        threading.excepthook = hook

    def al_error(self, exc: BaseException, tb, hilo: str = '?') -> bool:
        """Lanza un arreglo en otro hilo. False si un freno lo impide."""
        f = firma(exc, tb, self.raiz)
        if f in self._vistos:
            return False
        self._vistos.add(f)
        texto = ''.join(traceback.format_exception(type(exc), exc, tb))
        if not self._ocupado.acquire(blocking=False):
            errores.aviso('AUTOARREGLO', f'Ya estoy arreglando otro error: dejo pasar {f}.')
            return False

        def tarea() -> None:
            try:
                self.arreglar(texto, f, hilo)
            except Exception as e:
                errores.error('AUTOARREGLO', 'Falló el intento de arreglar mi código.', e)
            finally:
                self._ocupado.release()

        self._lanzar(tarea)
        return True

    # ── Arreglo ────────────────────────────────────────────────────────────────
    def arreglar(self, texto_error: str, firma_error: str, hilo: str) -> str:
        """Devuelve el resultado: 'propuesta', 'tope', 'sin_cambios', 'revertido' o 'listo'."""
        edicion = bool(self._en_ingeniero())
        if edicion and self._ediciones_hoy() >= MAX_POR_DIA:
            errores.aviso('AUTOARREGLO', f'Ya hice {MAX_POR_DIA} arreglos hoy: solo propongo.')
            edicion = False
        carpeta = self._carpeta(firma_error)
        informe = [f'# Arreglo de {firma_error}', '', f'Hilo: `{hilo}`. Modo: '
                   f'{"edición (modo ingeniero)" if edicion else "solo propuesta"}.', '',
                   '## Error', '', '```', texto_error.rstrip(), '```', '']
        pedido = self._pedido(texto_error, edicion)
        copias = Copias()

        if not edicion:
            errores.aviso('AUTOARREGLO', f'Error en mi código ({firma_error}): pido un '
                          'diagnóstico, sin editar.')
            respuesta = self._preguntar(pedido, copias, edicion=False)
            informe += ['## Diagnóstico y arreglo propuesto', '', respuesta or '(sin respuesta)']
            return self._cerrar(carpeta, informe, firma_error, 'propuesta', 'propuesta')

        errores.aviso('AUTOARREGLO', f'Error en mi código ({firma_error}): intento arreglarlo.')
        self._avisar('empiezo')
        respuesta = self._preguntar(pedido, copias, edicion=True)
        informe += ['## Respuesta de Claude', '', respuesta or '(sin respuesta)', '']
        editados = copias.actuales()
        cambio = diff(copias.originales, editados, self.raiz)
        if not cambio:
            copias.revertir()        # por si quedó un archivo nuevo vacío
            return self._cerrar(carpeta, informe, firma_error, 'sin_cambios', None)
        informe += ['## Cambio', '', '```diff', cambio.rstrip(), '```', '']
        self._guardar_copias(carpeta, copias)

        motivo = self._verificar(copias, editados, informe)
        if motivo:
            copias.revertir()
            errores.error('AUTOARREGLO', f'Mi arreglo no pasó la verificación: lo deshice. {motivo}')
            informe += [f'**Revertido:** {motivo}']
            return self._cerrar(carpeta, informe, firma_error, 'revertido', 'revertido')

        informe += ['**Aplicado.**']
        resultado = self._cerrar(carpeta, informe, firma_error, 'listo', 'listo')
        if self._supervisado:
            self._marcar_pendiente(carpeta, copias)
            self.reinicio_pedido = True
            errores.aviso('AUTOARREGLO', 'Arreglé mi código: me reinicio para aplicarlo.')
            self._pedir_reinicio()
        else:
            errores.aviso('AUTOARREGLO', 'Arreglé mi código; reinícia a Bob para aplicarlo '
                          '(sin supervisor no me reinicio solo).')
        return resultado

    def _verificar(self, copias: Copias, editados: Dict[str, Optional[bytes]],
                   informe: List[str]) -> Optional[str]:
        """None si el arreglo pasa: compila, importa y no rompe un test que pasaba."""
        rutas = [r for r, d in editados.items() if d is not None]
        borrar_bytecode(editados)
        error = self._compilar(rutas)
        if error:
            return error
        tests = tests_relacionados(list(editados), self.raiz)
        copias.revertir()
        borrar_bytecode(editados)
        try:
            antes = self._correr(tests)
        finally:
            Copias.poner(editados)
            borrar_bytecode(editados)
        despues = self._correr(tests)
        rotos = [t for t in tests if antes.get(t) and not despues.get(t)]
        informe += ['## Tests', ''] + [
            f'- `{os.path.basename(t)}`: antes {"OK" if antes.get(t) else "falla"}, '
            f'después {"OK" if despues.get(t) else "falla"}' for t in tests] + ['']
        if rotos:
            return 'Rompe ' + ', '.join(os.path.basename(t) for t in rotos) + '.'
        return None

    # ── Claude ─────────────────────────────────────────────────────────────────
    def _pedido(self, texto_error: str, edicion: bool) -> str:
        return (f'Error:\n```\n{texto_error.rstrip()}\n```\n\n'
                f'Últimas líneas de la bitácora de salud (logs/salud.jsonl):\n```\n'
                f'{self._cola_bitacora()}\n```')

    def _preguntar(self, pedido: str, copias: Copias, edicion: bool) -> Optional[str]:
        prompt = _PROMPT.format(raiz=self.raiz) + (_PROMPT_EDICION if edicion else _PROMPT_LECTURA)
        sesion = self._crear_sesion(prompt=prompt, edicion=edicion, raiz=self.raiz,
                                    aprobar=self._aprobador(copias) if edicion else None)
        try:
            return sesion.preguntar(pedido, timeout=TIMEOUT_CLAUDE_S,
                                    max_herramientas=MAX_HERRAMIENTAS)
        finally:
            sesion.cerrar()

    def _aprobador(self, copias: Copias):
        def aprobar(herramienta: str, entrada: dict):
            if herramienta not in ACCIONES:
                return f'{herramienta} no está permitido para arreglar código.'
            ruta = str(entrada.get('file_path') or '')
            motivo = prohibido(ruta, self.raiz) if ruta else 'falta la ruta'
            if motivo:
                errores.aviso('AUTOARREGLO', f'Bloqueé {herramienta} en '
                              f'{os.path.basename(ruta) or "?"}: {motivo}.')
                return f'No puedes editar ese archivo: {motivo}.'
            copias.guardar(ruta)
            print(f'[autoarreglo] {herramienta} {os.path.relpath(ruta, self.raiz)}', flush=True)
            return True
        return aprobar

    # ── Registro ───────────────────────────────────────────────────────────────
    def _carpeta(self, firma_error: str) -> str:
        sello = time.strftime('%Y%m%d-%H%M%S', time.localtime(self._reloj()))
        limpia = re.sub(r'[^A-Za-z0-9_.-]+', '_', firma_error)[:60]
        carpeta = os.path.join(self.dir_arreglos, f'{sello}-{limpia}')
        os.makedirs(carpeta, exist_ok=True)
        return carpeta

    def _guardar_copias(self, carpeta: str, copias: Copias) -> None:
        """Las copias en disco: con ellas revierte el supervisor si Bob no arranca."""
        destino = os.path.join(carpeta, 'copias')
        os.makedirs(destino, exist_ok=True)
        indice = {}
        for i, (ruta, datos) in enumerate(sorted(copias.originales.items())):
            archivo = None
            if datos is not None:
                archivo = os.path.join(destino, f'{i}-{os.path.basename(ruta)}')
                with open(archivo, 'wb') as f:
                    f.write(datos)
            indice[ruta] = archivo
        with open(os.path.join(carpeta, 'copias.json'), 'w', encoding='utf-8') as f:
            json.dump(indice, f, ensure_ascii=False, indent=1)

    def _marcar_pendiente(self, carpeta: str, copias: Copias) -> None:
        with open(os.path.join(self.dir_arreglos, 'pendiente.json'), 'w', encoding='utf-8') as f:
            json.dump({'carpeta': carpeta, 'copias': os.path.join(carpeta, 'copias.json')}, f,
                      ensure_ascii=False)

    def _cerrar(self, carpeta: str, informe: List[str], firma_error: str, resultado: str,
                evento: Optional[str]) -> str:
        with open(os.path.join(carpeta, 'informe.md'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(informe) + '\n')
        linea = {'t': round(self._reloj(), 3),
                 'fecha': time.strftime('%Y-%m-%d', time.localtime(self._reloj())),
                 'firma': firma_error, 'resultado': resultado,
                 'carpeta': os.path.basename(carpeta)}
        with open(os.path.join(self.dir_arreglos, 'registro.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(linea, ensure_ascii=False) + '\n')
        print(f'[autoarreglo] {resultado}: {os.path.relpath(carpeta, self.raiz)}', flush=True)
        if evento:
            self._avisar(evento)
        return resultado

    def _ediciones_hoy(self) -> int:
        hoy = time.strftime('%Y-%m-%d', time.localtime(self._reloj()))
        ruta = os.path.join(self.dir_arreglos, 'registro.jsonl')
        try:
            with open(ruta, encoding='utf-8') as f:
                lineas = [json.loads(x) for x in f if x.strip()]
        except (OSError, ValueError):
            return 0
        return sum(1 for x in lineas if x.get('fecha') == hoy
                   and x.get('resultado') in ('listo', 'revertido', 'sin_cambios'))

    def _cola_bitacora(self) -> str:
        try:
            with open(self._bitacora, encoding='utf-8') as f:
                return ''.join(f.readlines()[-LINEAS_BITACORA:]).rstrip() or '(vacía)'
        except OSError:
            return '(sin bitácora)'


def _sesion_claude(prompt: str, edicion: bool, raiz: str, aprobar=None):
    """Sesión de Claude Code para arreglar (o solo leer) el código de Bob. cwd fuera del
    repo (ver claude_code.py) y robot_bob/ como única carpeta de trabajo."""
    from agente import claude_code
    from agente.modo_dueno import LECTURA_PROHIBIDAS   # los secretos (.env) no se leen
    return claude_code.SesionClaude(
        prompt, modelo=MODELO, effort='medium', nombre='autoarreglo',
        herramientas=HERRAMIENTAS_EDICION if edicion else HERRAMIENTAS_LECTURA,
        permitidas=['Read', 'Glob', 'Grep'], prohibidas=LECTURA_PROHIBIDAS,
        add_dirs=[raiz], aprobar=aprobar)
