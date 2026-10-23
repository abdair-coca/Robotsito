"""
test_puente_cli.py — Puente Bob → CLI (nucleo/puente_cli.py), T1 del plan del CLI.

Corre un proceso hijo con stdout y stdin por pipe, como lo lanza bob-cli (Node):
  1. Con BOB_CLI=1 cada línea de stdout es un evento NDJSON válido.
  2. Rich (las dos formas de consola de Bob), print(), errores.error/aviso y stderr
     llegan con su origen, nivel y colores.
  3. Dos hilos imprimiendo a la vez no mezclan líneas.
  4. Los comandos de stdin se despachan; los ilegibles o desconocidos avisan.
  5. Sin BOB_CLI la salida es idéntica a la de no llamar a activar().

  python tests/unit/test_puente_cli.py
"""
import json
import os
import subprocess
import sys

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


# Script del hijo: imprime por todos los caminos que usa Bob. ACTIVAR se reemplaza
# para comparar con y sin activar().
HIJO = r'''
import sys, threading
sys.path.insert(0, RAIZ)
from nucleo import puente_cli
puente_cli.al_comando('eco', lambda c: print(f"eco {c['x']}"))
puente_cli.al_comando('roto', lambda c: 1 / 0)
fin = threading.Event()
puente_cli.al_comando('salir', lambda c: fin.set())
ACTIVAR
from rich.console import Console
import errores
como_config = Console(force_terminal=True, color_system="truecolor")   # config.console
como_pipeline = Console()                                              # consola.console
como_config.print('[red]rojo de config[/]')
como_pipeline.print('[dark_orange]naranja del pipeline[/]')
print('un print suelto')
print('dos', 'partes', end='')
print(' y fin')
errores.error('CÁMARA', 'No llega video.', 'timed out')
errores.aviso('CLAUDE', 'No respondió a tiempo.')
print('a stderr', file=sys.stderr)
def hilo(n):
    for i in range(50):
        print(f'hilo{n}-linea{i:02d}-' + 'x' * 40)
hs = [threading.Thread(target=hilo, args=(n,)) for n in (1, 2)]
[h.start() for h in hs]
[h.join() for h in hs]
if puente_cli.ACTIVO:
    print('salir llegó' if fin.wait(5) else 'salir NO llegó')
'''.replace('RAIZ', repr(RAIZ))

# Sin "salir" registrado: al cerrarse stdin, KeyboardInterrupt en el hilo principal
# para que corra el finally de main.py (servos a casa, COM3).
HIJO_SIN_SALIR = r'''
import sys, time
sys.path.insert(0, RAIZ)
from nucleo import puente_cli
puente_cli.activar()
try:
    time.sleep(30)
except KeyboardInterrupt:
    print('KeyboardInterrupt')
finally:
    print('cerrado limpio')
'''.replace('RAIZ', repr(RAIZ))

COMANDOS = '{"cmd":"eco","x":7}\nbasura\n{"cmd":"nada"}\n{"cmd":"roto"}\n'


def correr(activar: str, con_cli: bool, entrada: str = '',
           script: str = HIJO) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != 'BOB_CLI'}
    env['PYTHONIOENCODING'] = 'utf-8'
    if con_cli:
        env['BOB_CLI'] = '1'
    return subprocess.run([sys.executable, '-c', script.replace('ACTIVAR', activar)],
                          input=entrada.encode('utf-8'), capture_output=True,
                          env=env, cwd=RAIZ, timeout=60)


# ── 1-4. Con BOB_CLI=1 ──────────────────────────────────────────────────────────
# El hijo registra los comandos antes de activar(): el lector arranca ahí y ya
# encuentra en stdin lo que se manda desde el inicio.
r = correr('puente_cli.activar()', con_cli=True, entrada=COMANDOS)
lineas = r.stdout.decode('utf-8').splitlines()
eventos, malas = [], []
for linea in lineas:
    try:
        eventos.append(json.loads(linea))
    except ValueError:
        malas.append(linea)
check('cli: el hijo termina bien', r.returncode == 0, r.stderr.decode('utf-8', 'replace')[-300:])
check('cli: todo stdout es NDJSON', lineas and not malas, f'({len(lineas)} líneas, malas: {malas[:2]})')
check('cli: stderr de Python no queda crudo', r.stderr == b'', repr(r.stderr[:200]))


def buscar(texto):
    return next((e for e in eventos if texto in e.get('ansi', '')), None)


e = buscar('rojo de config')
check('rich (config): evento log de bob con rojo', e is not None and e['t'] == 'log'
      and e['origen'] == 'bob' and e['nivel'] == 'info' and '\x1b[31m' in e['ansi'], repr(e))
e = buscar('naranja del pipeline')
check('rich (pipeline): naranja real, no 16 colores', e is not None and '208' in e['ansi'], repr(e))
e = buscar('un print suelto')
check('print(): llega como evento', e is not None and e['ansi'] == 'un print suelto', repr(e))
e = buscar('dos partes')
check('print() en dos partes: una sola línea', e is not None and e['ansi'] == 'dos partes y fin', repr(e))
e = buscar('No llega video')
check('errores.error: nivel error con ✖', e is not None and e['nivel'] == 'error'
      and '✖' in e['ansi'] and 'CÁMARA' in e['ansi'], repr(e))
e = buscar('No respondió a tiempo')
check('errores.aviso: nivel aviso, naranja con ⚠', e is not None and e['nivel'] == 'aviso'
      and '⚠' in e['ansi'] and '208' in e['ansi'], repr(e))
e = buscar('a stderr')
check('stderr: origen stderr', e is not None and e['origen'] == 'stderr', repr(e))
check('nivel vuelve a info tras errores', buscar('a stderr')['nivel'] == 'info'
      and buscar('hilo1-linea00')['nivel'] == 'info')
hilos = [e['ansi'] for e in eventos if e['ansi'].startswith('hilo')]
enteras = all(len(x) == len('hilo1-linea00-') + 40 and x.endswith('x' * 40) for x in hilos)
check('hilos: 100 líneas enteras, sin mezclar', len(hilos) == 100 and enteras, f'({len(hilos)})')
check('comando: eco despachado', buscar('eco 7') is not None)
avisos = [e['ansi'] for e in eventos if e['nivel'] == 'aviso' and e['ansi'].startswith('[cli]')]
check('comandos: ilegible, desconocido y fallido avisan', len(avisos) == 3
      and 'ilegible' in avisos[0] and 'desconocido: nada' in avisos[1] and 'roto falló' in avisos[2],
      repr(avisos))
check('stdin cerrado: llama a salir', buscar('salir llegó') is not None)

# ── 4b. stdin cerrado sin "salir": cierre limpio por KeyboardInterrupt ─────────
import time   # noqa: E402
t0 = time.monotonic()
r = correr('', con_cli=True, script=HIJO_SIN_SALIR)
textos = [json.loads(l)['ansi'] for l in r.stdout.decode('utf-8').splitlines()]
check('stdin cerrado sin salir: corre el finally enseguida',
      textos == ['KeyboardInterrupt', 'cerrado limpio'] and time.monotonic() - t0 < 10,
      f'{textos} ({time.monotonic() - t0:.1f} s)')

# ── 4c. stdin abierto (como con el CLI) no traba la carga de DLLs ──────────────
# En Windows, un hilo bloqueado leyendo el pipe de stdin traba a quien consulte ese
# handle: el runtime de Fortran de scipy lo hace al cargarse y `import main` quedaba
# colgado (2026-10-23).
HIJO_DLL = r'''
import sys, time
sys.path.insert(0, RAIZ)
from nucleo import puente_cli
puente_cli.activar()
time.sleep(0.5)                 # el lector ya está bloqueado en stdin
t0 = time.monotonic()
import scipy.linalg
print(f'scipy.linalg cargó en {time.monotonic() - t0:.1f} s')
'''.replace('RAIZ', repr(RAIZ))
env = {k: v for k, v in os.environ.items()}
env.update(BOB_CLI='1', PYTHONIOENCODING='utf-8')
p = subprocess.Popen([sys.executable, '-c', HIJO_DLL], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, env=env, cwd=RAIZ)
try:
    linea = None
    import threading as _th          # la lectura con timeout: sin el arreglo, cuelga
    leida = []
    _th.Thread(target=lambda: leida.append(p.stdout.readline()), daemon=True).start()
    for _ in range(300):              # hasta 30 s
        if leida:
            break
        time.sleep(0.1)
    linea = json.loads(leida[0])['ansi'] if leida and leida[0] else None
finally:
    p.stdin.close()                   # recién ahora el "CLI" se va
    try:
        p.wait(timeout=15)
    except subprocess.TimeoutExpired:
        p.kill()
check('stdin abierto: scipy.linalg carga sin trabarse', linea is not None and 'cargó' in linea,
      repr(linea))

# ── 5. Sin BOB_CLI: idéntico a no llamar a activar() ────────────────────────────
sin_cli = correr('puente_cli.activar()', con_cli=False)
nunca = correr('pass', con_cli=False)
check('sin BOB_CLI: stdout idéntico', sin_cli.stdout == nunca.stdout and b'rojo de config' in nunca.stdout,
      f'({len(sin_cli.stdout)} vs {len(nunca.stdout)} bytes)')
check('sin BOB_CLI: stderr idéntico', sin_cli.stderr == nunca.stderr)
check('sin BOB_CLI: nada de NDJSON', b'"t": "log"' not in sin_cli.stdout)

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLO(S)'}")
sys.exit(1 if fallos else 0)
