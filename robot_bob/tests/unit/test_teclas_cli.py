"""
test_teclas_cli.py — Atajos del CLI (nucleo/teclas_cli.py), T3 del plan del CLI.

Sin cámara, micrófono ni CLI de verdad:
  1. Cada comando del CLI entra a la cola con el código que lee el loop de main.py.
  2. Q: con el loop corriendo marca `salida`; antes del loop es como Ctrl+C.
  3. Camino completo: comandos por stdin de un proceso hijo con BOB_CLI=1.
  4. disparar_turno(): solo en modo tecla y con Bob libre.
  5. En modo CLI no arranca el ENTER por msvcrt (le robaría teclas al CLI).

  python tests/unit/test_teclas_cli.py
"""
import json
import os
import subprocess
import sys
import threading

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, RAIZ)

from nucleo import puente_cli, teclas_cli   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


# ── 1. Un comando por tecla ─────────────────────────────────────────────────────
teclas_cli.registrar()
check('registra tecla y salir', puente_cli._comandos.get('tecla') is teclas_cli._tecla
      and puente_cli._comandos.get('salir') is teclas_cli._salir)
check('sin teclas: SIN_TECLA', teclas_cli.siguiente() == (teclas_cli.SIN_TECLA, None))
for letra in ('s', 'd', 'j', 'S'):
    teclas_cli._tecla({'cmd': 'tecla', 'tecla': letra})
    check(f'tecla {letra}: código de la minúscula', teclas_cli.siguiente() == (ord(letra.lower()), None))
teclas_cli._tecla({'cmd': 'tecla', 'tecla': 'e', 'nombre': '  Abdair '})
check('tecla e: con el nombre limpio', teclas_cli.siguiente() == (ord('e'), 'Abdair'))
teclas_cli._tecla({'cmd': 'tecla', 'tecla': 'turno'})
check('turno: TURNO', teclas_cli.siguiente() == (teclas_cli.TURNO, None))
try:
    teclas_cli._tecla({'cmd': 'tecla', 'tecla': 'x'})
    check('tecla desconocida: error para el aviso del puente', False)
except ValueError:
    check('tecla desconocida: error para el aviso del puente', True)
check('las teclas salen en orden y la cola queda vacía', teclas_cli.siguiente()[0] == teclas_cli.SIN_TECLA)

# ── 2. Q / salir ────────────────────────────────────────────────────────────────
try:
    teclas_cli._tecla({'cmd': 'tecla', 'tecla': 'q'})
    import time
    time.sleep(0.2)              # la señal llega al hilo principal
    check('Q antes del loop: como Ctrl+C', False, '(no llegó KeyboardInterrupt)')
except KeyboardInterrupt:
    check('Q antes del loop: como Ctrl+C', not teclas_cli.salida.is_set())
teclas_cli.en_loop.set()
teclas_cli._salir({'cmd': 'salir'})
check('salir con el loop corriendo: marca salida', teclas_cli.salida.is_set()
      and teclas_cli.siguiente()[0] == teclas_cli.SIN_TECLA)
teclas_cli.salida.clear()
teclas_cli._tecla({'cmd': 'tecla', 'tecla': 'Q'})
check('Q con el loop corriendo: marca salida', teclas_cli.salida.is_set())

# ── 3. Camino completo por stdin, como lo manda el CLI ──────────────────────────
HIJO = r'''
import sys
sys.path.insert(0, RAIZ)
from nucleo import puente_cli, teclas_cli
teclas_cli.registrar()
teclas_cli.en_loop.set()
puente_cli.activar()
teclas_cli.salida.wait(10)
vistas = []
while True:
    t = teclas_cli.siguiente()
    if t[0] == teclas_cli.SIN_TECLA:
        break
    vistas.append(t)
print(repr(vistas), teclas_cli.salida.is_set())
'''.replace('RAIZ', repr(RAIZ))
comandos = ''.join(json.dumps(c) + '\n' for c in (
    {'cmd': 'tecla', 'tecla': 's'}, {'cmd': 'tecla', 'tecla': 'd'}, {'cmd': 'tecla', 'tecla': 'j'},
    {'cmd': 'tecla', 'tecla': 'turno'}, {'cmd': 'tecla', 'tecla': 'e', 'nombre': 'María José'},
    {'cmd': 'tecla', 'tecla': 'q'}))
env = dict(os.environ, BOB_CLI='1', PYTHONIOENCODING='utf-8')
r = subprocess.run([sys.executable, '-c', HIJO], input=comandos.encode('utf-8'), capture_output=True,
                   env=env, cwd=RAIZ, timeout=60)
salida_hijo = [json.loads(l)['ansi'] for l in r.stdout.decode('utf-8').splitlines()]
esperado = repr([(ord('s'), None), (ord('d'), None), (ord('j'), None), (teclas_cli.TURNO, None),
                 (ord('e'), 'María José')]) + ' True'
check('stdin → cola: S, D, J, ENTER, E con nombre y Q', salida_hijo == [esperado],
      f'{salida_hijo} {r.stderr.decode("utf-8", "replace")[-200:]}')

# ── 4. disparar_turno ───────────────────────────────────────────────────────────
from voz import pipeline as vp   # noqa: E402


class FakeSM:
    def __init__(self):
        self.en_conversacion = False
        self.wakes = []
        self.ev_hablando = threading.Event()
        self.ev_escuchando = threading.Event()   # el hilo _loop del pipeline lo espera
        self.ev_pensando = threading.Event()
        self.bailando = threading.Event()
        self.musica_sonando = threading.Event()

    def notificar_wake_word(self, payload=None, saludar=True):
        self.wakes.append(payload)

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


modo_original = vp.TRIGGER_MODE
v = vp.VoicePipeline(FakeMgr(), FakeSM())
vp.TRIGGER_MODE = 'wake'
check('turno en modo wake: no hace nada', v.disparar_turno() is False and v._sm.wakes == [])
vp.TRIGGER_MODE = 'tecla'
check('turno en modo tecla: inicia el turno', v.disparar_turno() is True and v._sm.wakes == [None])
v._sm.en_conversacion = True
check('turno durante una charla: nada', v.disparar_turno() is False and len(v._sm.wakes) == 1)
v._sm.en_conversacion = False
v._muted.set()
check('turno mientras habla solo: nada', v.disparar_turno() is False and len(v._sm.wakes) == 1)
v._muted.clear()

# ── 5. Modo CLI: sin msvcrt ─────────────────────────────────────────────────────
v._mic_continuo = lambda: None
activo_original = puente_cli.ACTIVO
puente_cli.ACTIVO = True
v.iniciar_wake_monitor()
con_cli = [t.name for t in threading.enumerate() if t.name == 'tecla-trigger']
puente_cli.ACTIVO = False
v.iniciar_wake_monitor()
sin_cli = [t.name for t in threading.enumerate() if t.name == 'tecla-trigger']
v._detener.set()
puente_cli.ACTIVO = activo_original
vp.TRIGGER_MODE = modo_original
check('modo tecla con CLI: no arranca msvcrt', con_cli == [])
check('modo tecla sin CLI: arranca como siempre', sin_cli == ['tecla-trigger'])

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLO(S)'}")
sys.exit(1 if fallos else 0)
