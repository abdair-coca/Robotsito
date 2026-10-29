"""
test_salud.py — Chequeos y vigilante de salud (nucleo/salud.py), S1 de la Fase S.

Sin hardware ni red (cada parte es un objeto de prueba):
  1. Vigilante: arranca optimista, avisa una vez por cambio y no repite.
  2. marcar_caida() baja una parte al instante; la pasada siguiente la devuelve.
  3. Un chequeo que lanza cuenta como caído; un aviso que lanza no rompe nada.
  5. Serial, audio, red y Ollama según el estado del objeto.
  6. Hilos: falta uno, caído; todos, bien.
  7. Memoria: una base real abre; una ruta que no existe, caída (y no la crea).
  8. El hilo revisa solo y cerrar() lo detiene.

  python tests/unit/test_salud.py
"""
import os
import sqlite3
import sys
import tempfile
import time

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, RAIZ)

from nucleo import salud   # noqa: E402
from nucleo.salud import Chequeo, Resultado, Vigilante   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


# ── 1. Avisos por cambio ────────────────────────────────────────────────────────
avisos = []
bien = {'a': True, 'b': False}
v = Vigilante([Chequeo('a', lambda: Resultado(bien['a'])),
               Chequeo('b', lambda: Resultado(bien['b'], 'b caída'))],
              al_cambiar=lambda n, r: avisos.append((n, r.ok)))
estado = v.revisar_todo()
check('primera pasada: solo avisa la caída', avisos == [('b', False)])
check('estado de todas las partes', estado['a'].ok and not estado['b'].ok
      and estado['b'].detalle == 'b caída')
v.revisar_todo()
check('pasada igual: sin aviso nuevo', avisos == [('b', False)])
bien['b'] = True
v.revisar_todo()
check('vuelve b: avisa una vez', avisos == [('b', False), ('b', True)])
bien['a'] = False
v.revisar_todo()
v.revisar_todo()
check('se cae a: avisa una vez', avisos[-1] == ('a', False) and len(avisos) == 3)

# ── 2. marcar_caida ─────────────────────────────────────────────────────────────
bien['a'] = True
v.revisar_todo()
n = len(avisos)
v.marcar_caida('a', 'falló al usarla')
check('marcar_caida: baja al instante y avisa', avisos[n:] == [('a', False)]
      and v.estado()['a'].detalle == 'falló al usarla')
v.marcar_caida('a')
check('marcar_caida repetida: sin aviso', len(avisos) == n + 1)
v.revisar_todo()
check('la pasada siguiente la devuelve', avisos[-1] == ('a', True))

# ── 3. Chequeo y aviso que lanzan ───────────────────────────────────────────────
def _lanza():
    raise OSError('sin respuesta')


def _aviso_roto(nombre, r):
    raise RuntimeError('aviso roto')


r = Vigilante([Chequeo('x', _lanza)]).revisar_todo()['x']
check('chequeo que lanza: caído', not r.ok and 'OSError' in r.detalle, r.detalle)
r = Vigilante([Chequeo('x', lambda: True)]).revisar_todo()['x']
check('chequeo que no devuelve Resultado: caído', not r.ok)
v2 = Vigilante([Chequeo('x', lambda: Resultado(False))], al_cambiar=_aviso_roto)
check('aviso que lanza: el estado cambia igual', not v2.revisar_todo()['x'].ok)

# ── 5. Serial, audio, red, Ollama ───────────────────────────────────────────────
class _Serial:
    _modo = 'wifi'


s = _Serial()
check('serial por wifi: bien', salud.chequeo_serial(s).revisar() == Resultado(True, 'por wifi'))
s._modo = 'none'
check('serial sin transporte: caído', not salud.chequeo_serial(s).revisar().ok)
check('serial simulado: bien', salud.chequeo_serial(object()).revisar().ok)


class _Audio:
    connected = True


a = _Audio()
check('audio conectado: bien', salud.chequeo_audio(a).revisar().ok)
a.connected = False
check('audio cortado: caído', not salud.chequeo_audio(a).revisar().ok)


class _Red:
    en_linea = True


red = _Red()
check('red en línea: bien', salud.chequeo_red(red).revisar().ok)
red.en_linea = False
check('sin internet: caída', salud.chequeo_red(red).revisar() == Resultado(False, 'sin internet'))
check('ollama caído: caído',
      salud.chequeo_ollama(lambda: (False, 'no responde')).revisar()
      == Resultado(False, 'no responde'))
check('ollama bien: bien', salud.chequeo_ollama(lambda: (True, 'instalado')).revisar().ok)

# ── 6. Hilos ────────────────────────────────────────────────────────────────────
hilos = salud.chequeo_hilos(('wake-monitor', 'soliloquio'),
                            vivos=lambda: {'wake-monitor', 'MainThread'})
r = hilos.revisar()
check('falta un hilo: caído y lo nombra', not r.ok and 'soliloquio' in r.detalle, r.detalle)
hilos = salud.chequeo_hilos(('MainThread',))
check('hilo real vivo: bien', hilos.revisar().ok)

# ── 7. Memoria ──────────────────────────────────────────────────────────────────
with tempfile.TemporaryDirectory() as tmp:
    db = os.path.join(tmp, 'memoria prueba.db')
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE personas (id INTEGER)')
    con.commit()
    con.close()
    check('base real: bien', salud.chequeo_memoria(db).revisar().ok)
    falta = os.path.join(tmp, 'no_existe.db')
    r = Vigilante([salud.chequeo_memoria(falta)]).revisar_todo()['memoria']
    check('base que no existe: caída', not r.ok, r.detalle)
    check('y no la crea', not os.path.exists(falta))

# ── 9. Reparar: espera creciente, tope y rendirse (S2) ──────────────────────────
t = [0.0]
parte = {'ok': False}
intentos, rendidos, cola = [], [], []


def _arreglo_que_falla():
    intentos.append(t[0])
    raise OSError('sigue caída')


v4 = Vigilante([Chequeo('cam', lambda: Resultado(parte['ok'], 'muda'), _arreglo_que_falla)],
               al_rendirse=lambda n, r: rendidos.append(n), reloj=lambda: t[0],
               lanzar=lambda n, tarea: cola.append(tarea))
v4.revisar_todo()
check('caída con receta: lanza un arreglo', len(cola) == 1 and intentos == [])
v4.revisar_todo()
check('mientras corre: no lanza otro', len(cola) == 1)
cola.pop()()
check('el arreglo que lanza no rompe nada', intentos == [0.0])
v4.revisar_todo()
check('antes de la espera: no reintenta', cola == [])
for _ in range(salud.REPARAR_INTENTOS + 3):
    t[0] += salud.REPARAR_ESPERA_MAX_S
    v4.revisar_todo()
    while cola:
        cola.pop()()
check(f'tope: {salud.REPARAR_INTENTOS} intentos', len(intentos) == salud.REPARAR_INTENTOS,
      f'({len(intentos)})')
check('se rinde una vez y queda rota', rendidos == ['cam'] and v4.rotas() == {'cam'})
parte['ok'] = True
v4.revisar_todo()
check('vuelve sola: deja de estar rota', v4.rotas() == set())

t[0], intentos[:] = 0.0, []
parte['ok'] = False
v5 = Vigilante([Chequeo('cam', lambda: Resultado(parte['ok']), _arreglo_que_falla)],
               reloj=lambda: t[0], lanzar=lambda n, tarea: tarea())
for _ in range(4):
    v5.revisar_todo()
    t[0] += 1.0
espera1 = salud.REPARAR_ESPERA_S
t[0] = espera1
v5.revisar_todo()
t[0] = espera1 + 2 * espera1 - 0.5
v5.revisar_todo()
check('la espera se duplica', intentos == [0.0, espera1], f'{intentos}')
t[0] = espera1 + 2 * espera1
v5.revisar_todo()
check('pasada la espera doble: reintenta', len(intentos) == 3)
parte['ok'] = True
v5.revisar_todo()
parte['ok'] = False
n = len(intentos)
v5.revisar_todo()
check('caída nueva tras volver: cuenta desde cero', len(intentos) == n + 1)
v6 = Vigilante([Chequeo('x', lambda: Resultado(False))], lanzar=lambda n, tarea: tarea())
v6.revisar_todo()
check('caída sin receta: nunca se rinde', v6.rotas() == set())

# ── 10. Recetas ─────────────────────────────────────────────────────────────────
class _AudioIO:
    def __init__(self):
        self.ip, self.connected, self.pasos = '192.168.0.23', False, []

    def close(self):
        self.pasos.append('close')

    def connect(self, timeout_s):
        self.pasos.append(('connect', self.ip))
        self.connected = True


aud = _AudioIO()
salud.reparar_audio(aud, resolver=lambda: {'ESP32_IP': '192.168.0.30'})()
check('audio: cierra y reconecta a la IP nueva',
      aud.pasos == ['close', ('connect', '192.168.0.30')] and aud.connected, f'{aud.pasos}')

vueltas = []
hilo = salud.chequeo_hilo('prueba-salud', lambda: vueltas.append(1))
check('hilo que no corre: caído', not hilo.revisar().ok)
hilo.reparar()
time.sleep(0.1)
check('reparar relanza su loop con el nombre', vueltas == [1])

# ── 8. Hilo del vigilante ───────────────────────────────────────────────────────
pasadas = []
v3 = Vigilante([Chequeo('x', lambda: pasadas.append(1) or Resultado(True))], periodo_s=0.05)
v3.iniciar()
time.sleep(0.3)
check('el hilo revisa solo', len(pasadas) >= 2, f'({len(pasadas)} pasadas)')
v3.cerrar()
time.sleep(0.15)
n = len(pasadas)
time.sleep(0.2)
check('cerrar() detiene el hilo', len(pasadas) == n, f'({n} pasadas)')

print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
