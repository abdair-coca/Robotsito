"""
test_salud.py — Chequeos y vigilante de salud (nucleo/salud.py), S1 de la Fase S.

Sin hardware ni red (cada parte es un objeto de prueba):
  1. Vigilante: arranca optimista, avisa una vez por cambio y no repite.
  2. marcar_caida() baja una parte al instante; la pasada siguiente la devuelve.
  3. Un chequeo que lanza cuenta como caído; un aviso que lanza no rompe nada.
  4. Cámara: frames nuevos bien; el mismo frame o ninguno más de max_s, caída.
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

# ── 4. Cámara ───────────────────────────────────────────────────────────────────
reloj = [100.0]
frame = [None]
cam = salud.chequeo_camara(lambda: frame[0], max_s=5.0, reloj=lambda: reloj[0])
check('cámara recién creada: bien', cam.revisar().ok)
reloj[0] = 106.0
r = cam.revisar()
check('sin ningún frame más de max_s: caída', not r.ok, r.detalle)
frame[0] = object()
check('llega un frame: bien', cam.revisar().ok)
reloj[0] = 110.0
frame[0] = object()
check('frames nuevos seguidos: bien', cam.revisar().ok)
reloj[0] = 116.0
r = cam.revisar()
check('el mismo frame más de max_s: caída', not r.ok and '6 s' in r.detalle, r.detalle)

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
