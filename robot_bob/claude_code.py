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

CWD_DEFAULT = os.path.join(tempfile.gettempdir(), 'bob_claude')
# Variables que no deben llegar al hijo: CLAUDECODE haría que se niegue a correr
# dentro de otra sesión de Claude Code; ANTHROPIC_API_KEY lo cobraría como API.
_ENV_FUERA = ('CLAUDECODE', 'ANTHROPIC_API_KEY')


def disponible() -> bool:
    return shutil.which('claude') is not None


class SesionClaude:
    def __init__(self, system_prompt: str, modelo: str = 'claude-haiku-5-5',
                 effort: str = 'low', mcp: Optional[dict] = None,
                 permitidas: Sequence[str] = (), prohibidas: Sequence[str] = (),
                 cwd: str = CWD_DEFAULT, nombre: str = 'claude', herramientas: str = '',
                 comando: Optional[list] = None, add_dirs: Sequence[str] = ()):
        """mcp: {'nombre': {'command': ..., 'args': [...]}} (formato mcpServers).
        herramientas: herramientas propias de Claude Code habilitadas ('' = ninguna;
        p. ej. 'WebSearch', que busca del lado de Anthropic y no toca la laptop).
        add_dirs: carpetas extra donde las herramientas de archivo pueden trabajar
        (con --restricted, nada fuera de cwd y de estas carpetas).
        comando: reemplaza el comando completo (solo para tests)."""
        self.system_prompt = system_prompt
        self.modelo, self.effort = modelo, effort
        self.mcp = mcp
        self.permitidas, self.prohibidas = list(permitidas), list(prohibidas)
        self.cwd = cwd
        self.nombre = nombre
        self.herramientas = herramientas
        self.add_dirs = list(add_dirs)
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
            cmd += ['--permission-mode', 'dontAsk']
            if self.permitidas:
                cmd += ['--allowedTools', ' '.join(self.permitidas)]
            if self.prohibidas:
                cmd += ['--disallowedTools', ' '.join(self.prohibidas)]
        if self.add_dirs:
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
                if self.mcp:
                    with open(os.path.join(self.cwd, f'{self.nombre}-mcp.json'), 'w',
                              encoding='utf-8') as f:
                        json.dump({'mcpServers': self.mcp}, f)
                log = open(os.path.join(self.cwd, f'{self.nombre}-stderr.log'), 'a',
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
                print(f'[claude] {self.nombre}: no arrancó ({e})')
                self._proc = None
                return False
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
    def preguntar(self, texto: str, timeout: float = 30.0,
                  al_usar: Optional[Callable[[str, dict], None]] = None,
                  max_herramientas: Optional[int] = None) -> Optional[str]:
        """Manda un mensaje y espera el resultado del turno. Devuelve el texto
        final, o None si falla, se pasa de tiempo o de herramientas (en esos dos
        casos cierra el proceso: el siguiente turno lo vuelve a arrancar)."""
        with self._turno:
            if not self.iniciar():
                return None
            eventos = self._eventos
            try:
                self._proc.stdin.write(json.dumps(
                    {'type': 'user', 'message': {'role': 'user', 'content': texto}},
                    ensure_ascii=False) + '\n')
                self._proc.stdin.flush()
            except Exception as e:
                print(f'[claude] {self.nombre}: no se pudo escribir ({e})')
                self.cerrar()
                return None
            partes, usos = [], 0
            limite = time.monotonic() + timeout
            while True:
                restante = limite - time.monotonic()
                if restante <= 0:
                    print(f'[claude] {self.nombre}: sin respuesta en {timeout:.0f} s')
                    self.cerrar()
                    return None
                try:
                    ev = eventos.get(timeout=restante)
                except queue.Empty:
                    continue
                if ev is None:
                    print(f'[claude] {self.nombre}: el proceso terminó')
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
                                print(f'[claude] {self.nombre}: más de {max_herramientas} '
                                      'herramientas, se corta')
                                self.cerrar()
                                return None
                elif tipo == 'result':
                    if ev.get('is_error'):
                        print(f'[claude] {self.nombre}: error ({ev.get("subtype")})')
                        return None
                    texto_final = (ev.get('result') or ''.join(partes)).strip()
                    return texto_final or None
