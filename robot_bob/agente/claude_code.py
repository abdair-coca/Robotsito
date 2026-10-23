"""
claude_code.py — Sesión persistente de Claude Code (`claude -p`) para Bob (Fase V5).

Usa la suscripción Pro del dueño: el CLI de Claude Code con su login, sin
ANTHROPIC_API_KEY (si estuviera, se cobraría como API). Un proceso por sesión,
con entrada y salida stream-json: el arranque (~3 s) se paga una vez y cada
turno tarda ~1–1.5 s con Haiku 5.5 a effort low (medido en la laptop).

Aislado del entorno de programación del dueño:
  --setting-sources ""   sin settings, hooks ni CLAUDE.md del usuario
  --system-prompt        el prompt de Bob reemplaza el de Claude Code
  --tools ""             sin herramientas propias (leer, editar, bash…)
  --strict-mcp-config    solo los servidores MCP que se pasan
  cwd fuera del repo     desde robot_bob/ se colaría la memoria automática
(--bare aislaría más, pero exige API key: no lee el login de Pro.)

Con servidores MCP (agente web), --permission-mode dontAsk rechaza toda
herramienta fuera de `permitidas`, y `prohibidas` ni se le muestran al modelo.
"""

import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional, Sequence

import errores

CWD_DEFAULT = os.path.join(tempfile.gettempdir(), 'bob_claude')
# Variables que no deben llegar al hijo: CLAUDECODE haría que se niegue a correr
# dentro de otra sesión de Claude Code; ANTHROPIC_API_KEY lo cobraría como API.
_ENV_FUERA = ('CLAUDECODE', 'ANTHROPIC_API_KEY')
CORTE_S = 10.0      # espera del fin de un turno interrumpido antes de cerrar el proceso


def disponible() -> bool:
    return shutil.which('claude') is not None


class SesionClaude:
    def __init__(self, system_prompt: str, modelo: str = 'claude-haiku-5-5',
                 effort: str = 'low', mcp: Optional[dict] = None,
                 permitidas: Sequence[str] = (), prohibidas: Sequence[str] = (),
                 cwd: str = CWD_DEFAULT, nombre: str = 'claude', herramientas: str = '',
                 comando: Optional[list] = None, add_dirs: Sequence[str] = (),
                 aprobar: Optional[Callable[[str, dict], object]] = None,
                 restringido: bool = False, conservar: bool = False):
        """mcp: {'nombre': {'command': ..., 'args': [...]}} (formato mcpServers).
        herramientas: herramientas propias de Claude Code habilitadas ('' = ninguna;
        p. ej. 'WebSearch', que busca del lado de Anthropic y no toca la laptop).
        add_dirs: carpetas extra donde las herramientas de archivo pueden trabajar
        (con --restricted, nada fuera de cwd y de estas carpetas).
        aprobar: decide cada pedido de permiso (herramienta, entrada) → True (sí), False
        (no) o un texto (no, y Claude recibe ese texto como motivo). Con
        aprobar, Claude pide permiso al host (B2) en vez de denegar solo; sin decisor
        o si falla, se deniega.
        restringido: --restricted aunque no haya add_dirs (archivos solo en cwd).
        conservar: si el turno se pasa de tiempo o de herramientas, se interrumpe y el
        proceso sigue (el navegador del MCP queda abierto); sin esto se cierra.
        comando: reemplaza el comando completo (solo para tests)."""
        self.system_prompt = system_prompt
        self.modelo, self.effort = modelo, effort
        self.mcp = mcp
        self.permitidas, self.prohibidas = list(permitidas), list(prohibidas)
        self.cwd = cwd
        self.nombre = nombre
        self.herramientas = herramientas
        self.add_dirs = list(add_dirs)
        self.aprobar = aprobar
        self.restringido = restringido
        self.conservar = conservar
        self.arranques = 0                   # procesos lanzados: uno nuevo no sabe nada de la charla
        self.turnos = 0                      # turnos respondidos por el proceso actual
        self._comando_fijo = comando
        self._proc: Optional[subprocess.Popen] = None
        self._eventos: queue.Queue = queue.Queue()
        self._turno = threading.Lock()       # un turno a la vez
        self._arranque = threading.Lock()

    # ── Proceso ─────────────────────────────────────────────────────────────
    def comando(self) -> list:
        if self._comando_fijo:
            return list(self._comando_fijo)
        cmd = [shutil.which('claude') or 'claude', '-p',
               '--model', self.modelo, '--effort', self.effort,
               '--setting-sources', '', '--tools', self.herramientas, '--strict-mcp-config',
               '--no-session-persistence',
               '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
               '--system-prompt', self.system_prompt]
        if self.mcp:
            cmd += ['--mcp-config', os.path.join(self.cwd, f'{self.nombre}-mcp.json')]
        if self.mcp or self.herramientas:
            if self.aprobar is None:
                cmd += ['--permission-mode', 'dontAsk']
            else:                            # B3: cada pedido sale al host (B2, B-D7)
                cmd += ['--permission-mode', 'default', '--permission-prompt-tool', 'stdio']
            if self.permitidas:
                cmd += ['--allowedTools', ' '.join(self.permitidas)]
            if self.prohibidas:
                cmd += ['--disallowedTools', ' '.join(self.prohibidas)]
        if self.add_dirs or self.restringido:
            cmd += ['--restricted']          # archivos confinados a cwd y --add-dir
            for carpeta in self.add_dirs:
                cmd += ['--add-dir', carpeta]
        return cmd

    @staticmethod
    def entorno() -> dict:
        return {k: v for k, v in os.environ.items() if k not in _ENV_FUERA}

    @property
    def vivo(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def iniciar(self) -> bool:
        """Arranca el proceso (idempotente). False si no se pudo."""
        with self._arranque:
            if self.vivo:
                return True
            try:
                os.makedirs(self.cwd, exist_ok=True)
                os.makedirs(CWD_DEFAULT, exist_ok=True)
                if self.mcp:
                    with open(os.path.join(self.cwd, f'{self.nombre}-mcp.json'), 'w',
                              encoding='utf-8') as f:
                        json.dump({'mcpServers': self.mcp}, f)
                # El stderr va a la carpeta común, no al cwd: en AppInglés no debe aparecer
                # ningún archivo de Bob.
                log = open(os.path.join(CWD_DEFAULT, f'{self.nombre}-stderr.log'), 'a',
                           encoding='utf-8')
                self._eventos = queue.Queue()
                try:
                    self._proc = subprocess.Popen(
                        self.comando(), cwd=self.cwd, env=self.entorno(),
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                        text=True, encoding='utf-8', bufsize=1,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                finally:
                    log.close()          # el hijo ya tiene su propio handle
            except Exception as e:
                errores.error('CLAUDE', f'La sesión "{self.nombre}" no arrancó. Revisa que Claude '
                              'Code esté instalado y con la sesión iniciada.', e)
                self._proc = None
                return False
            self.arranques += 1
            self.turnos = 0
            threading.Thread(target=self._leer, args=(self._proc, self._eventos),
                             daemon=True, name=f'claude-{self.nombre}').start()
            return True

    def iniciar_en_fondo(self) -> None:
        threading.Thread(target=self.iniciar, daemon=True,
                         name=f'claude-{self.nombre}-arranque').start()

    @staticmethod
    def _leer(proc, eventos) -> None:
        try:
            for linea in proc.stdout:
                linea = linea.strip()
                if not linea:
                    continue
                try:
                    eventos.put(json.loads(linea))
                except ValueError:
                    continue
        except Exception:
            pass
        eventos.put(None)            # el proceso terminó

    def cerrar(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    # ── Turnos ──────────────────────────────────────────────────────────────
    def _responder_permiso(self, proc, ev: dict) -> None:
        """Pedido de permiso del CLI (B2, B-D7): el decisor dice sí o no. Sin decisor, o si
        falla, se niega: el turno nunca queda colgado esperando."""
        req = ev.get('request') or {}
        resp: dict = {}
        if req.get('subtype') == 'can_use_tool':
            decision = False
            if self.aprobar is not None:
                try:
                    decision = self.aprobar(req.get('tool_name', ''), req.get('input') or {})
                except Exception as e:
                    errores.aviso('CLAUDE', f'Sesión "{self.nombre}": falló el control de permisos; '
                                  'por seguridad niego esa acción.', e)
            if decision is True:
                resp = {'behavior': 'allow', 'updatedInput': req.get('input') or {}}
            else:     # un texto: se niega con ese mensaje (p. ej. "Bob ya lo hizo")
                resp = {'behavior': 'deny', 'message': decision if isinstance(decision, str) and decision
                        else 'Bob no permitió esta acción'}
        salida = {'type': 'control_response',
                  'response': {'subtype': 'success', 'request_id': ev.get('request_id'),
                               'response': resp}}
        try:
            proc.stdin.write(json.dumps(salida, ensure_ascii=False) + '\n')
            proc.stdin.flush()
        except Exception as e:
            errores.aviso('CLAUDE', f'Sesión "{self.nombre}": no pude contestarle un permiso; el '
                          'turno puede cortarse por tiempo.', e)

    def _cortar(self, eventos) -> None:
        """Turno pasado de tiempo o de herramientas. Con `conservar`, lo interrumpe y espera
        su fin (el proceso y su memoria siguen); si no, o si no responde, cierra el proceso:
        el siguiente turno lo vuelve a arrancar."""
        if self.conservar and self.vivo:
            try:
                self._proc.stdin.write(json.dumps(
                    {'type': 'control_request', 'request_id': f'corte-{time.monotonic_ns()}',
                     'request': {'subtype': 'interrupt'}}) + '\n')
                self._proc.stdin.flush()
                limite = time.monotonic() + CORTE_S
                while time.monotonic() < limite:
                    try:
                        ev = eventos.get(timeout=max(0.05, limite - time.monotonic()))
                    except queue.Empty:
                        break
                    if ev is None:
                        break
                    if ev.get('type') == 'control_request':
                        self._responder_permiso(self._proc, ev)
                    elif ev.get('type') == 'result':
                        return
            except Exception as e:
                errores.aviso('CLAUDE', f'Sesión "{self.nombre}": no pude frenar el turno; la '
                              'reinicio.', e)
        self.cerrar()

    def preguntar(self, texto: str, timeout: float = 30.0,
                  al_usar: Optional[Callable[[str, dict], None]] = None,
                  max_herramientas: Optional[int] = None) -> Optional[str]:
        """Manda un mensaje y espera el resultado del turno. Devuelve el texto
        final, o None si falla, se pasa de tiempo o de herramientas (en esos dos
        casos corta el turno, ver _cortar)."""
        with self._turno:
            if not self.iniciar():
                return None
            eventos = self._eventos
            while True:                      # restos de un turno interrumpido (_cortar)
                try:
                    if eventos.get_nowait() is None:
                        eventos.put(None)    # el proceso terminó: lo ve el bucle de abajo
                        break
                except queue.Empty:
                    break
            try:
                self._proc.stdin.write(json.dumps(
                    {'type': 'user', 'message': {'role': 'user', 'content': texto}},
                    ensure_ascii=False) + '\n')
                self._proc.stdin.flush()
            except Exception as e:
                errores.aviso('CLAUDE', f'Sesión "{self.nombre}": no pude mandarle el mensaje; la '
                              'reinicio para el próximo turno.', e)
                self.cerrar()
                return None
            partes, usos = [], 0
            limite = time.monotonic() + timeout
            while True:
                restante = limite - time.monotonic()
                if restante <= 0:
                    errores.aviso('CLAUDE', f'Sesión "{self.nombre}": no respondió en {timeout:.0f} s; '
                                  'corto el turno.')
                    self._cortar(eventos)
                    return None
                try:
                    ev = eventos.get(timeout=restante)
                except queue.Empty:
                    continue
                if ev is None:
                    errores.aviso('CLAUDE', f'Sesión "{self.nombre}": el proceso se cerró solo; el '
                                  'próximo turno lo vuelve a abrir.')
                    self._proc = None
                    return None
                tipo = ev.get('type')
                if tipo == 'assistant':
                    for b in (ev.get('message') or {}).get('content', []):
                        if b.get('type') == 'text':
                            partes.append(b.get('text', ''))
                        elif b.get('type') == 'tool_use':
                            usos += 1
                            if al_usar:
                                al_usar(b.get('name', ''), b.get('input') or {})
                            if max_herramientas is not None and usos > max_herramientas:
                                errores.aviso('CLAUDE', f'Sesión "{self.nombre}": usó más de '
                                              f'{max_herramientas} herramientas; corto el turno.')
                                self._cortar(eventos)
                                return None
                elif tipo == 'control_request':
                    self._responder_permiso(self._proc, ev)
                elif tipo == 'result':
                    if ev.get('is_error'):
                        errores.aviso('CLAUDE', f'Sesión "{self.nombre}": el turno terminó con error.',
                                      ev.get('subtype'))
                        return None
                    self.turnos += 1
                    texto_final = (ev.get('result') or ''.join(partes)).strip()
                    return texto_final or None
