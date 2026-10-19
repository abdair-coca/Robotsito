"""
test_lectura_workspace.py — Lectura del modo ingeniero sobre APPINGLES_DIR (fase B1), sin red.

Revisa el comando de Claude Code que arma _crear_sesion_ingeniero: Read, Glob y Grep
dentro de la carpeta, --restricted, .env prohibido, sin Edit, Write ni Bash. Sin
APPINGLES_DIR la sesión no tiene herramientas, como antes. La prueba con Claude real
está en test_lectura_live.py.

  python tests/test_lectura_workspace.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agente import claude_code   # noqa: E402
from agente import modo_dueno   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class VPFalso:
    _memoria = None


def valor(cmd: list, flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


original = modo_dueno.APPINGLES_DIR
carpeta = tempfile.mkdtemp(prefix='AppInglesTest')
try:
    # 1) Sin carpeta: como antes, sin herramientas
    modo_dueno.APPINGLES_DIR = ''
    cmd = modo_dueno._crear_sesion_ingeniero(VPFalso()).comando()
    check("sin carpeta: sin --add-dir ni --restricted",
          '--add-dir' not in cmd and '--restricted' not in cmd)
    check("sin carpeta: --tools vacío", valor(cmd, '--tools') == '')

    # 2) Con carpeta: lectura confinada
    modo_dueno.APPINGLES_DIR = carpeta
    sesion = modo_dueno._crear_sesion_ingeniero(VPFalso())
    cmd = sesion.comando()
    check("con carpeta: cwd propio, no la temporal compartida",
          sesion.cwd != claude_code.CWD_DEFAULT and os.path.basename(sesion.cwd) == 'lectura')
    check("con carpeta: --add-dir es la carpeta", valor(cmd, '--add-dir') == carpeta,
          f"-> {valor(cmd, '--add-dir') if '--add-dir' in cmd else '-'}")
    check("con carpeta: --restricted", '--restricted' in cmd)
    check("con carpeta: --tools Read,Glob,Grep", valor(cmd, '--tools') == 'Read,Glob,Grep',
          f"-> {valor(cmd, '--tools')}")
    check("con carpeta: --allowedTools solo lectura", valor(cmd, '--allowedTools') == 'Read Glob Grep')
    check("con carpeta: dontAsk", valor(cmd, '--permission-mode') == 'dontAsk')
    check("con carpeta: .env prohibido", 'Read(**/.env)' in valor(cmd, '--disallowedTools').split())
    check("con carpeta: sin Edit, Write ni Bash",
          not {'Edit', 'Write', 'Bash'} & set(valor(cmd, '--tools').split(','))
          and not {'Edit', 'Write', 'Bash'} & set(valor(cmd, '--allowedTools').split()))
    check("con carpeta: sigue sin ajustes del usuario", valor(cmd, '--setting-sources') == '')
    check("con carpeta: el prompt no revela la ruta", carpeta not in valor(cmd, '--system-prompt'))
finally:
    modo_dueno.APPINGLES_DIR = original
    shutil.rmtree(carpeta, ignore_errors=True)

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
