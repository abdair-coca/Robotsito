"""
test_bash_workspace.py — Bash libre del modo ingeniero con los bloqueos de B-D3 (fases B4, B-D9), sin red.

Cubre: la tabla de comandos (permitidos y bloqueados, incluido salir de AppInglés), el decisor
(Bash sin voz, salvo bloqueo), el comando de la sesión (Bash en --tools, fuera de --allowedTools)
y el protocolo completo contra un proceso falso que pide ejecutar un comando. La prueba con
Claude real está en test_bash_live.py.

  python tests/unit/test_bash_workspace.py
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

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
                  'npm run dev', 'npm test', 'node server.js', 'git diff', 'mkdir proyecto',
                  # npm instala en el proyecto, desde el registro
                  'npm install', 'npm ci', 'npm install react', 'npm i vite', 'npm add -D vitest',
                  'npm install && npm run dev',
                  # redirecciones: su & no encadena nada
                  'npm install 2>&1 | tail -n 5', 'npm ci 2>&1', 'npm test &> salida.txt',
                  # ruta del script entre comillas (lo que Claude manda en Windows)
                  'python "C:\\Users\\abdai\\desktop\\AppInglés\\src\\calculadora.py"',
                  'py "src/app.py"', 'python "C:\\Mi Carpeta\\app.py" --modo prueba',
                  'node "src\\server.js"',
                  'python -I "C:/Users/abdai/Temp/src/calculadora.py"', 'python -u -B app.py',
                  "python 'src\\calculadora.py'", "node 'src/server.js'"]
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
                  'npx create-vite app', 'powershell -c dir',
                  # npm: sin globales ni paquetes desde una URL
                  'npm install -g vite', 'npm i --global x', 'npm install --location=global x',
                  'npm install --prefix C:\\x react', 'npm install https://x.com/p.tgz',
                  'npm i git+https://github.com/x/y', 'npm install github:x/y',
                  'node -e "1"', 'python -c "import os"', 'start https://vite.dev', 'dir | cmd',
                  # con comillas no se cuela una expansión, un encadenado ni otro tipo de archivo
                  'python "$(evil).py"', 'python "`evil`.py"', 'python "%TEMP%\\x.py"',
                  'python "a.py; rm x"', 'python "a.py" & start x', 'python "datos.txt"',
                  'python "C:\\x\\.env.py"', 'node "x.js" | cmd',
                  # las opciones que ejecutan código siguen fuera
                  'python -c "print(1)"', 'python -m http.server', 'python -Ic "x" a.py',
                  'python -I -m pip list', "python '$HOME/x.py'", "python 'a.py'; start x"]
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
    check("decisor: npm install corre sin pedir nada",
          decidir('Bash', {'command': 'npm install'}) is True)
    check("aviso: npm install dice que instala",
          modo_dueno._aviso('Bash', {'command': 'npm install'})[0] == 'Estoy instalando las dependencias.')

    # 2a) Servidor de desarrollo: lo levanta Bob y Claude recibe la dirección
    for c in ('npm run dev', 'npm run dev &', 'npm start', 'npm run dev -- --port 3000',
              'npm run dev 2>&1', 'npm run preview'):
        check(f"servidor: {c}", modo_dueno.es_servidor(c))
    for c in ('npm run build', 'npm run dev && rm x', 'npm run dev; curl x', 'npm run dev | cmd',
              'npm run dev -- --prefix C:\\', 'npm install'):
        check(f"no es servidor: {c}", not modo_dueno.es_servidor(c))
    orig_iniciar = modo_dueno.iniciar_servidor
    modo_dueno.iniciar_servidor = lambda c: ('http://localhost:5173/', '')
    try:
        r = decidir('Bash', {'command': 'npm run dev'})
        check("decisor: npm run dev → Bob lo levanta y devuelve la dirección",
              isinstance(r, str) and 'http://localhost:5173/' in r, f"-> {r}")
        check("aviso: npm run dev dice que levanta la app",
              modo_dueno._aviso('Bash', {'command': 'npm run dev'})[0] == 'Estoy levantando la app.')
    finally:
        modo_dueno.iniciar_servidor = orig_iniciar
    # servidor real: un script que dice su dirección y se queda corriendo
    with open(os.path.join(carpeta, 'servidor.py'), 'w', encoding='utf-8') as f:
        f.write('import time\nprint("  Local:   http://localhost:5999/", flush=True)\ntime.sleep(60)\n')
    orig_dir = modo_dueno.APPINGLES_DIR
    modo_dueno.APPINGLES_DIR = carpeta
    try:
        url, motivo = modo_dueno.iniciar_servidor(f'"{sys.executable}" servidor.py')
        proc = modo_dueno._servidor['proc']
        check("servidor real: da su dirección", url == 'http://localhost:5999/', f"-> {url} {motivo}")
        check("servidor real: si ya corre, no arranca otro",
              modo_dueno.iniciar_servidor('x')[0] == url and modo_dueno._servidor['proc'] is proc)
        modo_dueno.detener_servidor()
        time.sleep(0.5)
        check("servidor real: detener lo apaga", proc.poll() is not None and modo_dueno.servidor_url() is None)
        with open(os.path.join(carpeta, 'muere.py'), 'w', encoding='utf-8') as f:
            f.write('print("Error: falta vite")\n')
        url, motivo = modo_dueno.iniciar_servidor(f'"{sys.executable}" muere.py')
        check("servidor que muere: dice por qué", url is None and 'falta vite' in motivo, f"-> {motivo}")
    finally:
        modo_dueno.detener_servidor()
        modo_dueno.APPINGLES_DIR = orig_dir

    # 2b) Tiempo del turno: instalar tarda; si Bob lo ofreció, el "sí" también tiene más tiempo
    class S:
        def __init__(self, nombre):
            self.nombre = nombre
    vp = VPFalso()
    vp._convo = [{'role': 'assistant', 'content': 'Listo, ¿quieres que haga algo más?'}]
    largo, corto = modo_dueno.ING_INSTALAR_TIMEOUT_S, modo_dueno.ING_TIMEOUT_S
    check("tiempo: instalar en escritura → largo",
          modo_dueno._timeout_turno(vp, 'instala las dependencias', S('escritura')) == largo)
    check("tiempo: charla normal → corto",
          modo_dueno._timeout_turno(vp, 'qué hay en App.jsx', S('escritura')) == corto)
    check("tiempo: lectura no instala → corto",
          modo_dueno._timeout_turno(vp, 'instala las dependencias', S('lectura')) == corto)
    vp._convo.append({'role': 'assistant', 'content': '¿Quieres que instale las dependencias?'})
    check("tiempo: 'sí' tras ofrecer instalar → largo",
          modo_dueno._timeout_turno(vp, 'sí, hazlo', S('escritura')) == largo)

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

# 5) B5: la salida de un comando no se lee entera, aunque Claude la copie sin puntuación
salida = '\n'.join(f'-a---  {i:03d}  archivo_{i}.txt  {i * 137} bytes' for i in range(200))
tope = modo_dueno.ING_MAX_FRASES * modo_dueno.FRASE_MAX_CHARS
for nombre, texto in (('200 líneas sin puntos', salida),
                      ('intro + 200 líneas', 'Listé la carpeta:\n' + salida),
                      ('200 líneas con puntos', salida.replace(' bytes', ' bytes.'))):
    dichas = modo_dueno.frases_para_decir(texto, modo_dueno.ING_MAX_FRASES)
    check(f"B5: {nombre} → hasta {modo_dueno.ING_MAX_FRASES} frases",
          0 < len(dichas) <= modo_dueno.ING_MAX_FRASES, f"-> {len(dichas)}")
    check(f"B5: {nombre} → no más de {tope} caracteres en total",
          sum(len(f) for f in dichas) <= tope, f"-> {sum(len(f) for f in dichas)}")
resumen = 'Hay 200 archivos en la carpeta. El más pesado es informe.txt.'
check("B5: un resumen normal queda intacto",
      modo_dueno.frases_para_decir(resumen, 4) ==
      ['Hay 200 archivos en la carpeta.', 'El más pesado es informe.txt.'])
check("B5: lo acortado termina en punto y en palabra completa",
      all(f.endswith('.') and not f.endswith(' .')
          for f in modo_dueno.frases_para_decir(salida, 4)))

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
