"""
fabrica/ciclo.py — F2 a F6 de la fase F: Bob toma una tarea, la programa, la prueba y la
commitea en su rama.

Un ciclo (ciclo() = elegir() + trabajar()):
  F1. elegir(): la primera tarea pendiente y automática de .dev/PLAN.md (fabrica/backlog.py)
      que no tenga ya una rama bob/<id>-…. Si la tiene, ya se intentó: espera al dueño.
  F2. Un worktree nuevo en .fabrica/, con la rama bob/<id>-<título> desde la rama base: el
      código vivo de Bob no se toca. Se copian config.py y devices.json, que están
      gitignorados y sin ellos no importa la mitad del código. .env no se copia.
  F3. Claude Code (Sonnet, F-D5) con Read, Glob, Grep, Edit y Write, confinado al robot_bob/
      del worktree. Cada Edit y Write pasa por aprobar(): zonas prohibidas (F-D4, F-D6) y
      copia previa del archivo.
  F4. Compuertas: hay cambios; hay un test en tests/unit (F-D2); compila e importa; los tests
      que pasaban siguen pasando y los suyos pasan; un segundo Claude, solo con lectura,
      aprueba el diff (F-D3).
  F5. Commit en la rama de Bob, con autor "Bob" y el correo del dueño (B-D5). Sin push ni
      merge (F-D1).
  F6. Aviso (avisar), informe en logs/fabrica/ y una línea en .dev/TASK.md.
Si una compuerta falla no hay commit: el worktree se borra y queda el informe con el diff.
La rama queda, así la tarea no se reintenta sola.

Frenos: una tarea a la vez, MAX_POR_DIA intentos por día, apagada con el perfil feria (F-D7).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

import errores
import rutas
from agente.autoarreglo import (Copias, borrar_bytecode, compila_e_importa, correr_tests, diff,
                                prohibido, tests_relacionados)
from fabrica import backlog

try:
    from config import PERFIL
except Exception:
    PERFIL = 'normal'
try:
    from config import FABRICA_ENABLED
except Exception:
    FABRICA_ENABLED = True

REPO = os.path.dirname(rutas.RAIZ)
RAMA_BASE = 'bob-feria-octubre'
MODELO = 'claude-sonnet-5-5'              # F-D5, como el autoarreglo (S-D11)
MAX_POR_DIA = 3                           # F-D5
TIMEOUT_CLAUDE_S = 900.0                  # 15 min por tarea (F-D5)
MAX_HERRAMIENTAS = 80
TIMEOUT_REVISION_S = 300.0
MAX_HERRAMIENTAS_REVISION = 30
AUTOR = ('Bob', 'cocaabdair@gmail.com')   # B-D5: se distingue en el historial y cuenta para la racha
COPIAR = ('config.py', 'devices.json')    # gitignorados que el código necesita para importar
ACCIONES = ('Edit', 'Write')
# Resultados que gastaron un turno de Claude: cuentan para el tope del día.
INTENTOS = ('listo', 'sin_cambios', 'sin_test', 'no_compila', 'rompe', 'test_falla',
            'rechazado', 'error')

FRASES = {   # borrador: los textos de voz los corrige el dueño
    'empiezo': 'Me pongo con la tarea {id}: {titulo}. Te aviso cuando termine.',
    'listo': 'Terminé la tarea {id} en mi rama. Los tests pasan y el revisor la aprobó. '
             '¿La revisas?',
    'fallo': 'No pude terminar la tarea {id}: {motivo} Dejé el informe en mis registros.',
    'sin_tareas': 'No tengo tareas automáticas pendientes en mi lista.',
    'tope': 'Ya trabajé {n} tareas hoy. Sigo mañana.',
    'apagada': 'Con el perfil de feria no me programo.',
    'ocupada': 'Ya estoy trabajando en una tarea.',
}

_PROMPT_TAREA = (
    'Eres el programador de Bob, un robot humanoide en Python. Trabajas en una copia aparte '
    'de su código (carpeta {raiz}), en una rama que después revisa otro Claude y al final el '
    'dueño. Implementa la tarea con el cambio mínimo, en el estilo del código que la rodea: '
    'nombres y comentarios en español, con la misma densidad de comentarios. Reutiliza lo que '
    'ya existe antes de crear algo nuevo. '
    'Escribe un test nuevo (o amplía uno) en tests/unit/ que pruebe el criterio de la tarea. '
    'Los tests son scripts, no pytest: imprimen [OK] o [FALLO] por caso, terminan con '
    '"TODO OK" si todo pasa y salen con sys.exit(1) si algo falla; mira los que ya hay en '
    'tests/unit/. El test corre sin red, sin micrófono y sin el robot. '
    'No agregues dependencias ni toques requirements. No edites .env, memory.db, '
    'reminders.db, config.py ni devices.json, y nunca leas ni cites .env ni claves. No '
    'ejecutes nada: Bob compila y corre los tests después. Si la tarea no se puede hacer sin '
    'hardware o sin el dueño, no edites y explica por qué. '
    'Al terminar, responde en tres líneas: archivos cambiados, qué hiciste y qué prueba el test.')
_PROMPT_REVISION = (
    'Eres el revisor del código de Bob, un robot humanoide en Python (carpeta {raiz}). Otro '
    'Claude implementó una tarea en una rama de Bob; tú decides si se commitea. Lee el diff y, '
    'si hace falta, el código de alrededor. Rechaza si no cumple la tarea o su criterio, si '
    'tiene un bug, si el test no prueba de verdad lo pedido (o lo hace pasar a la fuerza), si '
    'toca secretos o agrega dependencias, o si cambia más de lo necesario. No edites nada. '
    'Responde en la primera línea solo APROBADO o RECHAZADO: <motivo en una frase>. Debajo, '
    'si quieres, los detalles.')

_LOCK = threading.Lock()                  # una tarea a la vez en este proceso


@dataclass
class Resultado:
    resultado: str      # elegida, listo, sin_tareas, tope, apagada, ocupada o uno de INTENTOS
    tarea: Optional[backlog.Tarea] = None
    rama: str = ''
    motivo: str = ''
    commit: str = ''
    carpeta: str = ''


def _slug(texto: str, largo: int = 40) -> str:
    sin_tildes = ''.join(c for c in unicodedata.normalize('NFKD', texto.lower())
                         if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]+', '-', sin_tildes).strip('-')[:largo].strip('-')


def nombre_rama(tarea: backlog.Tarea) -> str:
    """bob/<id>-<título>: el id va primero, así elegir() reconoce las ya intentadas."""
    slug = _slug(tarea.titulo)
    return f'bob/{tarea.id.lower()}-{slug}' if slug else f'bob/{tarea.id.lower()}'


def prohibido_fabrica(ruta: str, raiz: str) -> Optional[str]:
    """Las zonas del autoarreglo (S-D7) más las de la fábrica (F-D4 y F-D6)."""
    motivo = prohibido(ruta, raiz)
    if motivo:
        return motivo
    nombre = os.path.basename(ruta).lower()
    if nombre.startswith('reminders.db'):
        return 'son los recordatorios de Bob'
    if nombre.startswith('requirements') and nombre.endswith('.txt'):
        return 'las dependencias nuevas las decide el dueño'
    return None


def sin_crlf(contenidos: Dict[str, Optional[bytes]]) -> Dict[str, Optional[bytes]]:
    """En Windows los archivos están en CRLF y Claude a veces escribe en LF: sin esto, el diff
    (y el revisor) ve el archivo entero reescrito (medido en la prueba en vivo)."""
    return {r: (d.replace(b'\r\n', b'\n') if d is not None else None)
            for r, d in contenidos.items()}


def veredicto(respuesta: Optional[str]) -> Tuple[bool, str]:
    """(aprobado, motivo) según la primera línea del revisor: APROBADO o RECHAZADO: motivo."""
    if not respuesta:
        return False, 'el revisor no respondió.'
    linea = next((x.strip(' *#_`') for x in respuesta.splitlines() if x.strip(' *#_`')), '')
    if linea.upper().startswith('APROBADO'):
        return True, ''
    if linea.upper().startswith('RECHAZADO'):
        motivo = linea.split(':', 1)[1].strip() if ':' in linea else ''
        return False, (motivo or 'el revisor la rechazó').rstrip('.') + '.'
    return False, 'el revisor no dio un veredicto claro.'


class Fabrica:
    def __init__(self, repo: str = REPO, rel_bob: str = 'robot_bob', base: str = RAMA_BASE,
                 plan: Optional[str] = None, tarea_md: Optional[str] = None,
                 crear_sesion: Optional[Callable[..., object]] = None,
                 correr: Optional[Callable[[List[str], str], Dict[str, bool]]] = None,
                 compilar: Optional[Callable[[Iterable[str], str], Optional[str]]] = None,
                 avisar: Callable[[str, str], None] = lambda evento, texto: None,
                 apagada: Optional[Callable[[], bool]] = None,
                 max_por_dia: int = MAX_POR_DIA, reloj: Callable[[], float] = time.time,
                 dir_registro: Optional[str] = None) -> None:
        """avisar(evento, texto): evento es una clave de FRASES; texto, la frase ya armada."""
        self.repo = repo
        self.rel_bob = rel_bob
        self.raiz_viva = os.path.join(repo, rel_bob)
        self.base = base
        self.plan = plan or os.path.join(repo, '.dev', 'PLAN.md')
        self.tarea_md = tarea_md or os.path.join(repo, '.dev', 'TASK.md')
        self.worktrees = os.path.join(repo, '.fabrica')
        self.dir_registro = dir_registro or os.path.join(self.raiz_viva, 'logs', 'fabrica')
        self._crear_sesion = crear_sesion or _sesion_claude
        self._correr = correr or correr_tests
        self._compilar = compilar or compila_e_importa
        self._avisar = avisar
        self._apagada = apagada or (lambda: PERFIL == 'feria' or not FABRICA_ENABLED)
        self.max_por_dia = max_por_dia
        self._reloj = reloj

    # ── F1: elegir ─────────────────────────────────────────────────────────────
    def elegir(self) -> Resultado:
        """La tarea que toca ('elegida'), o por qué no hay ninguna. Avisa en los dos casos."""
        if self._apagada():
            return self._avisar_y(Resultado('apagada'))
        if _LOCK.locked():
            return self._avisar_y(Resultado('ocupada'))
        hechas = self._intentos_hoy()
        if hechas >= self.max_por_dia:
            return self._avisar_y(Resultado('tope'), n=hechas)
        try:
            with open(self.plan, encoding='utf-8') as f:
                texto = f.read()
        except OSError as e:
            return self._avisar_y(Resultado('sin_tareas', motivo=f'no pude leer el plan: {e}'))
        tarea = backlog.siguiente(texto, saltar=self._ids_con_rama())
        if tarea is None:
            return self._avisar_y(Resultado('sin_tareas'))
        self._avisar('empiezo', FRASES['empiezo'].format(id=tarea.id, titulo=tarea.titulo))
        return Resultado('elegida', tarea, nombre_rama(tarea))

    def ciclo(self) -> Resultado:
        """Elige una tarea y la trabaja (lo que usa la terminal)."""
        r = self.elegir()
        return self.trabajar(r.tarea) if r.resultado == 'elegida' else r

    # ── F2 a F6: trabajar ──────────────────────────────────────────────────────
    def trabajar(self, tarea: backlog.Tarea) -> Resultado:
        """Programa, prueba y commitea una tarea en su rama. Nunca lanza: nada de la
        fábrica tumba a Bob."""
        if not _LOCK.acquire(blocking=False):
            return self._avisar_y(Resultado('ocupada', tarea))
        try:
            return self._trabajar(tarea)
        except Exception as e:
            errores.error('FABRICA', f'Falló la tarea {tarea.id}.', e)
            return Resultado('error', tarea, nombre_rama(tarea), motivo=f'{type(e).__name__}: {e}')
        finally:
            _LOCK.release()

    def _trabajar(self, tarea: backlog.Tarea) -> Resultado:
        rama = nombre_rama(tarea)
        r = Resultado('error', tarea, rama, carpeta=self._carpeta(tarea))
        informe = [f'# Tarea {tarea.id}: {tarea.titulo}', '',
                   f'Rama: `{rama}`, desde `{self.base}`.', f'Criterio: {tarea.criterio}', '']
        wt = os.path.join(self.worktrees, rama.replace('/', '-'))
        try:
            self._git(self.repo, 'worktree', 'add', '-q', '-b', rama, wt, self.base)    # F2
        except RuntimeError as e:
            r.motivo = f'no pude crear la rama: {str(e).rstrip(".")}.'
            return self._cerrar(r, informe)
        try:
            raiz_wt = os.path.join(wt, self.rel_bob)
            self._copiar_config(raiz_wt)
            copias = Copias()
            respuesta = self._implementar(tarea, raiz_wt, copias)                          # F3
            informe += ['## Respuesta de Claude', '', respuesta or '(sin respuesta)', '']
            r.resultado, r.motivo = self._compuertas(tarea, wt, raiz_wt, copias, informe)  # F4
            if r.resultado == 'listo':
                r.commit = self._commit(wt, tarea, copias)                                 # F5
                informe += [f'**Commit:** `{r.commit}` en `{rama}`.']
            else:
                informe += [f'**Sin commit:** {r.motivo}']
        except Exception as e:
            errores.error('FABRICA', f'Falló la tarea {tarea.id}.', e)
            r.resultado, r.motivo = 'error', f'{type(e).__name__}: {e}.'
            informe += [f'**Error:** {r.motivo}']
        finally:
            self._quitar_worktree(wt)
        return self._cerrar(r, informe)                                                    # F6

    # ── F3: implementar ────────────────────────────────────────────────────────
    def _implementar(self, tarea: backlog.Tarea, raiz_wt: str, copias: Copias) -> Optional[str]:
        sesion = self._crear_sesion(prompt=_PROMPT_TAREA.format(raiz=raiz_wt), raiz=raiz_wt,
                                    aprobar=self._aprobador(raiz_wt, copias), edicion=True)
        try:
            return sesion.preguntar(self._pedido(tarea), timeout=TIMEOUT_CLAUDE_S,
                                    max_herramientas=MAX_HERRAMIENTAS)
        finally:
            sesion.cerrar()

    @staticmethod
    def _pedido(tarea: backlog.Tarea) -> str:
        partes = [f'Tarea {tarea.id}: {tarea.titulo}']
        if tarea.detalle:
            partes.append(tarea.detalle)
        partes.append(f'Criterio (lo que tiene que probar tu test): {tarea.criterio}')
        return '\n'.join(partes)

    @staticmethod
    def _aprobador(raiz_wt: str, copias: Copias):
        def aprobar(herramienta: str, entrada: dict):
            if herramienta not in ACCIONES:
                return f'{herramienta} no está permitido en la fábrica.'
            ruta = str(entrada.get('file_path') or '')
            motivo = prohibido_fabrica(ruta, raiz_wt) if ruta else 'falta la ruta'
            if motivo:
                errores.aviso('FABRICA', f'Bloqueé {herramienta} en '
                              f'{os.path.basename(ruta) or "?"}: {motivo}.')
                return f'No puedes editar ese archivo: {motivo}.'
            copias.guardar(ruta)
            print(f'[fabrica] {herramienta} {os.path.relpath(ruta, raiz_wt)}', flush=True)
            return True
        return aprobar

    # ── F4: compuertas ─────────────────────────────────────────────────────────
    def _compuertas(self, tarea: backlog.Tarea, wt: str, raiz_wt: str, copias: Copias,
                    informe: List[str]) -> Tuple[str, str]:
        """('listo', '') si el cambio pasa todo; si no, (resultado, motivo)."""
        editados = copias.actuales()
        cambio = diff(sin_crlf(copias.originales), sin_crlf(editados), raiz_wt)
        if not cambio:
            return 'sin_cambios', 'no cambió ningún archivo.'
        informe += ['## Cambio', '', '```diff', cambio.rstrip(), '```', '']
        sueltos = self._cambios_git(wt) - set(editados)
        if sueltos:
            return 'error', ('hay cambios que no pasaron por el control de permisos: ' +
                             ', '.join(sorted(os.path.relpath(s, wt) for s in sueltos)) + '.')
        unit = os.path.realpath(os.path.join(raiz_wt, 'tests', 'unit'))
        propios = {r for r, d in editados.items() if d is not None and os.path.dirname(r) == unit
                   and os.path.basename(r).startswith('test_')}
        if not propios:
            return 'sin_test', 'no escribió un test en tests/unit.'
        borrar_bytecode(editados)
        error = self._compilar([r for r, d in editados.items() if d is not None], raiz_wt)
        if error:
            return 'no_compila', error.rstrip('.') + '.'
        tests = tests_relacionados(list(editados), raiz_wt)
        copias.revertir()
        borrar_bytecode(editados)
        try:
            antes = self._correr(tests, raiz_wt)
        finally:
            Copias.poner(editados)
            borrar_bytecode(editados)
        despues = self._correr(tests, raiz_wt)
        informe += ['## Tests', ''] + [
            f'- `{os.path.basename(t)}`: antes {"OK" if antes.get(t) else "falla"}, '
            f'después {"OK" if despues.get(t) else "falla"}' for t in tests] + ['']
        rotos = [t for t in tests if antes.get(t) and not despues.get(t)]
        if rotos:
            return 'rompe', 'rompe ' + ', '.join(os.path.basename(t) for t in rotos) + '.'
        fallan = [t for t in tests if t in propios and not despues.get(t)]
        if fallan:
            return 'test_falla', ('su test no pasa: ' +
                                  ', '.join(os.path.basename(t) for t in fallan) + '.')
        aprobado, motivo = self._revisar(tarea, raiz_wt, cambio, informe)
        return ('listo', '') if aprobado else ('rechazado', motivo)

    def _revisar(self, tarea: backlog.Tarea, raiz_wt: str, cambio: str,
                 informe: List[str]) -> Tuple[bool, str]:
        sesion = self._crear_sesion(prompt=_PROMPT_REVISION.format(raiz=raiz_wt), raiz=raiz_wt,
                                    aprobar=None, edicion=False)
        try:
            respuesta = sesion.preguntar(
                f'{self._pedido(tarea)}\n\nDiff:\n```diff\n{cambio.rstrip()}\n```',
                timeout=TIMEOUT_REVISION_S, max_herramientas=MAX_HERRAMIENTAS_REVISION)
        finally:
            sesion.cerrar()
        informe += ['## Revisión', '', respuesta or '(sin respuesta)', '']
        return veredicto(respuesta)

    def _cambios_git(self, wt: str) -> Set[str]:
        """Archivos cambiados o nuevos en el worktree, según git (lo ignorado no cuenta)."""
        salida = self._git(wt, '-c', 'core.quotepath=false', 'status', '--porcelain',
                           '--untracked-files=all')
        rutas_wt = set()
        for linea in salida.splitlines():
            ruta = linea[3:].split(' -> ')[-1].strip().strip('"')
            if ruta:
                rutas_wt.add(os.path.realpath(os.path.join(wt, ruta)))
        return rutas_wt

    # ── F5: commit ─────────────────────────────────────────────────────────────
    def _commit(self, wt: str, tarea: backlog.Tarea, copias: Copias) -> str:
        rels = sorted(os.path.relpath(r, wt).replace(os.sep, '/')
                      for r, d in copias.actuales().items() if d is not None)
        self._git(wt, 'add', '--', *rels)
        titulo = tarea.titulo[:1].lower() + tarea.titulo[1:]
        self._git(wt, '-c', f'user.name={AUTOR[0]}', '-c', f'user.email={AUTOR[1]}',
                  'commit', '-q', '-m', f'feat({tarea.id.lower()}): {titulo}')
        return self._git(wt, 'rev-parse', '--short', 'HEAD').strip()

    # ── F6: registro y aviso ───────────────────────────────────────────────────
    def _cerrar(self, r: Resultado, informe: List[str]) -> Resultado:
        os.makedirs(r.carpeta, exist_ok=True)
        with open(os.path.join(r.carpeta, 'informe.md'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(informe) + '\n')
        fecha = time.strftime('%Y-%m-%d', time.localtime(self._reloj()))
        linea = {'t': round(self._reloj(), 3), 'fecha': fecha, 'tarea': r.tarea.id,
                 'rama': r.rama, 'resultado': r.resultado, 'motivo': r.motivo,
                 'commit': r.commit, 'carpeta': os.path.basename(r.carpeta)}
        with open(os.path.join(self.dir_registro, 'registro.jsonl'), 'a', encoding='utf-8') as f:
            f.write(json.dumps(linea, ensure_ascii=False) + '\n')
        self._anotar_tarea(fecha, r)
        print(f'[fabrica] {r.resultado}: {r.tarea.id} en {r.rama}'
              f'{" (" + r.motivo + ")" if r.motivo else ""}', flush=True)
        if r.resultado == 'listo':
            self._avisar('listo', FRASES['listo'].format(id=r.tarea.id))
        else:
            self._avisar('fallo', FRASES['fallo'].format(id=r.tarea.id, motivo=r.motivo))
        return r

    def _anotar_tarea(self, fecha: str, r: Resultado) -> None:
        """Una línea en .dev/TASK.md, si existe (F5)."""
        if not os.path.exists(self.tarea_md):
            return
        estado = f'commit {r.commit}' if r.commit else f'sin commit: {r.motivo}'
        with open(self.tarea_md, 'a', encoding='utf-8') as f:
            f.write(f'\n- {fecha} fábrica: {r.tarea.id} en `{r.rama}`, {r.resultado} ({estado}) '
                    f'Informe: logs/fabrica/{os.path.basename(r.carpeta)}/informe.md\n')

    def _avisar_y(self, r: Resultado, **datos) -> Resultado:
        self._avisar(r.resultado, FRASES[r.resultado].format(**datos))
        return r

    # ── Ayudantes ──────────────────────────────────────────────────────────────
    def _carpeta(self, tarea: backlog.Tarea) -> str:
        sello = time.strftime('%Y%m%d-%H%M%S', time.localtime(self._reloj()))
        return os.path.join(self.dir_registro, f'{sello}-{tarea.id.lower()}')

    def _intentos_hoy(self) -> int:
        hoy = time.strftime('%Y-%m-%d', time.localtime(self._reloj()))
        try:
            with open(os.path.join(self.dir_registro, 'registro.jsonl'), encoding='utf-8') as f:
                lineas = [json.loads(x) for x in f if x.strip()]
        except (OSError, ValueError):
            return 0
        return sum(1 for x in lineas if x.get('fecha') == hoy and x.get('resultado') in INTENTOS)

    def _ids_con_rama(self) -> Set[str]:
        """Ids de las tareas que ya tienen una rama bob/<id>-…: ya se intentaron."""
        salida = self._git(self.repo, 'for-each-ref', '--format=%(refname:short)', 'refs/heads/bob/')
        return {x[len('bob/'):].split('-')[0] for x in salida.split() if x.startswith('bob/')}

    def _copiar_config(self, raiz_wt: str) -> None:
        for nombre in COPIAR:
            origen = os.path.join(self.raiz_viva, nombre)
            if os.path.exists(origen):
                shutil.copy2(origen, os.path.join(raiz_wt, nombre))

    def _quitar_worktree(self, wt: str) -> None:
        try:
            self._git(self.repo, 'worktree', 'remove', '--force', wt)
        except RuntimeError as e:
            errores.aviso('FABRICA', f'No pude borrar el worktree {os.path.basename(wt)}.', e)
        try:
            self._git(self.repo, 'worktree', 'prune')
        except RuntimeError:
            pass

    @staticmethod
    def _git(donde: str, *args: str) -> str:
        r = subprocess.run(['git', '-C', donde, *args], capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        if r.returncode != 0:
            lineas = (r.stderr or r.stdout).strip().splitlines()
            raise RuntimeError(lineas[-1] if lineas else 'git falló')
        return r.stdout


def _sesion_claude(prompt: str, raiz: str, aprobar=None, edicion: bool = True):
    """Sesión de Claude Code para programar (o solo revisar) en el worktree. cwd fuera del
    repo (ver claude_code.py) y el robot_bob/ del worktree como única carpeta de trabajo."""
    from agente import claude_code
    from agente.modo_dueno import LECTURA_PROHIBIDAS   # los secretos (.env) no se leen
    return claude_code.SesionClaude(
        prompt, modelo=MODELO, effort='medium',
        nombre='fabrica' if edicion else 'fabrica-revision',
        herramientas='Read,Glob,Grep,Edit,Write' if edicion else 'Read,Glob,Grep',
        permitidas=['Read', 'Glob', 'Grep'], prohibidas=LECTURA_PROHIBIDAS,
        add_dirs=[raiz], aprobar=aprobar)
