"""
test_bash_workspace.py — Bash libre del modo ingeniero con los bloqueos de B-D3 (fases B4, B-D9), sin red.

Cubre: la tabla de comandos (permitidos y bloqueados, incluido salir de AppInglés), el decisor
(Bash sin voz, salvo bloqueo), el comando de la sesión (Bash en --tools, fuera de --allowedTools)
y el protocolo completo contra un proceso falso que pide ejecutar un comando. La prueba con
Claude real está en test_bash_live.py.

  python tests/test_bash_workspace.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from agente import claude_code   # noqa: E402
from agente import modo_dueno    # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class MemoriaFalsa:
    def contexto(self, persona_id):
        return ''


class VPFalso:
    def __init__(self):
        self._memoria = MemoriaFalsa()
        self._modo_ingeniero = True
        self._sesion_ing = None
        self._contexto_web = None
        self._client = None


def valor(cmd: list, flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


# Proceso falso: pide permiso para ejecutar el comando de argv[1] y termina el turno con
# el behavior que recibió.
HIJO_BASH = r'''
import json, sys
for linea in sys.stdin:
    ev = json.loads(linea)
    if ev.get("type") == "user":
        print(json.dumps({"type": "control_request", "request_id": "b1",
              "request": {"subtype": "can_use_tool", "tool_name": "Bash",
                          "input": {"command": sys.argv[1]}}}), flush=True)
        ctl = json.loads(sys.stdin.readline())
        beh = ctl["response"]["response"].get("behavior", "vacio")
        print(json.dumps({"type": "result", "is_error": False, "result": beh}), flush=True)
        break
'''

original_apdir = modo_dueno.APPINGLES_DIR
carpeta = tempfile.mkdtemp(prefix='AppInglesBash')
try:
    modo_dueno.APPINGLES_DIR = carpeta
    os.makedirs(os.path.join(carpeta, 'subcarpeta'))

    # 1) Tabla de comandos
    permitidos = ['dir', 'ls', 'git status', 'git log --oneline', 'cat nota.txt', 'type nota.txt',
                  'python script.py', 'rm nota.txt', 'cd subcarpeta', 'cd .',
                  'Set-Location subcarpeta', 'dir && git status',
                  'npm run dev', 'npm test', 'node server.js', 'git diff', 'mkdir proyecto']
    bloqueados = ['git push origin main', 'git reset --hard HEAD', 'git clean -fd',
                  'git branch -D viejo', 'git branch -d viejo', 'type .env', 'copy .env copia.txt',
                  'cat C:\\x\\.env', 'rm -rf carpeta', 'rm -r carpeta', 'rm -fr carpeta',
                  'Remove-Item carpeta -Recurse', 'rd /s /q carpeta', 'del /s *.txt',
                  'pip install requests', 'python -m pip install x', 'curl http://x', 'wget x',
                  'powershell Invoke-WebRequest x', 'cd C:\\Windows', 'cd ..', 'cd',
                  'cd %USERPROFILE%', 'cd ~', 'dir & cd C:\\', 'Set-Location C:\\Users',
                  'pushd C:\\',
                  # B-D10: fuera de la allowlist, aunque no caigan en BASH_BLOQUEADOS
                  'cmd /c start https://vite.dev', 'cmd.exe /c start "" "https://vite.dev"',
                  'npm install react', 'npm i vite', 'npx create-vite app', 'powershell -c dir',
                  'node -e "1"', 'python -c "import os"', 'start https://vite.dev', 'dir | cmd']
    for c in permitidos:
        check(f"permitido: {c}", not modo_dueno.es_bash_bloqueado(c))
    for c in bloqueados:
        check(f"bloqueado: {c}", modo_dueno.es_bash_bloqueado(c))

    # 2) Decisor: Bash sin voz, salvo bloqueo; herramientas fuera de la lista, no
    decidir = modo_dueno._aprobar_escritura()
    check("decisor: dir corre sin pedir nada", decidir('Bash', {'command': 'dir'}) is True)
    check("decisor: git push se niega", decidir('Bash', {'command': 'git push origin main'}) is False)
    check("decisor: Edit y Write siguen permitidos",
          decidir('Edit', {'file_path': 'n.txt'}) is True and decidir('Write', {'file_path': 'n.txt'}) is True)
    check("decisor: WebSearch se niega", decidir('WebSearch', {'query': 'x'}) is False)

    # 3) Comando de la sesión: Bash en --tools, pero pide permiso (no está en --allowedTools)
    cmd = modo_dueno._sesion_escritura(VPFalso()).comando()
    check("sesión: Bash en --tools", 'Bash' in valor(cmd, '--tools').split(','))
    check("sesión: Bash no está pre-aprobado", 'Bash' not in valor(cmd, '--allowedTools').split())
    check("sesión: pide permiso al host", valor(cmd, '--permission-prompt-tool') == 'stdio')
    reglas = valor(cmd, '--disallowedTools')
    check("sesión: reglas CLI para leer .env con cat, type y similares",
          'Bash(cat *.env*)' in reglas and 'Bash(type *.env*)' in reglas, f"-> {reglas[:120]}")
    check("sesión: reglas CLI para git push, pip install, curl",
          all(r in reglas for r in ('Bash(git push *)', 'Bash(pip install *)', 'Bash(curl *)')))
    check("sesión: reglas CLI para lectura ampliada (xxd, base64, robocopy, Format-Hex, bat)",
          all(r in reglas for r in ('Bash(xxd *.env*)', 'Bash(base64 *.env*)', 'Bash(robocopy *.env*)',
                                    'Bash(Format-Hex *.env*)', 'Bash(bat *.env*)')))

    # 4) Protocolo: el host decide cada comando
    def correr(comando_bash):
        s = claude_code.SesionClaude('x', nombre='bash', cwd=carpeta,
                                     comando=[sys.executable, '-c', HIJO_BASH, comando_bash],
                                     aprobar=modo_dueno._aprobar_escritura())
        try:
            return s.preguntar('ejecuta', timeout=30)
        finally:
            s.cerrar()

    check("protocolo: dir → allow", correr('dir') == 'allow', f"-> {correr('dir')}")
    check("protocolo: git push → deny", correr('git push origin main') == 'deny')
    check("protocolo: cambiar a C:\\ → deny", correr('cd C:\\') == 'deny')
finally:
    modo_dueno.APPINGLES_DIR = original_apdir
    shutil.rmtree(carpeta, ignore_errors=True)

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
