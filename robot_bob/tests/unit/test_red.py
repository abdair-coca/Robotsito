"""
test_red.py — Detector de red (hardware/red.py), O1 del plan del modo sin internet.

Sin red de verdad (el sondeo es una función de prueba), salvo el punto 5:
  1. Arranca con red; un sondeo sin conexión la baja y avisa una vez.
  2. Sondeos iguales seguidos no repiten el aviso; la vuelta avisa True.
  3. marcar_caida() baja la red sin sondear; repetida no avisa de nuevo.
  4. Un aviso que lanza no rompe el monitor.
  5. hay_internet() con un puerto cerrado de localhost da False.
  6. El hilo sondea solo y cerrar() lo detiene.

  python tests/unit/test_red.py
"""
import os
import sys
import threading
import time

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, RAIZ)

from hardware import red   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


# ── 1 y 2. Sondeos ──────────────────────────────────────────────────────────────
avisos = []
hay = [False]
m = red.MonitorRed(al_cambiar=avisos.append, sondear=lambda: hay[0])
check('arranca con red', m.en_linea)
check('sondeo sin conexión: sin red', m.revisar() is False and avisos == [False])
m.revisar()
check('segundo sondeo igual: sin aviso nuevo', avisos == [False])
hay[0] = True
check('vuelve la red: avisa True', m.revisar() is True and avisos == [False, True])
m.revisar()
check('con red estable: sin aviso nuevo', avisos == [False, True])

# ── 3. marcar_caida ─────────────────────────────────────────────────────────────
m.marcar_caida()
check('marcar_caida: sin red y avisa', not m.en_linea and avisos == [False, True, False])
m.marcar_caida()
check('marcar_caida repetida: sin aviso', avisos == [False, True, False])
check('el sondeo la devuelve', m.revisar() is True and avisos[-1] is True)

# ── 4. Aviso que lanza ──────────────────────────────────────────────────────────
def _explota(_):
    raise RuntimeError('aviso roto')


m2 = red.MonitorRed(al_cambiar=_explota, sondear=lambda: False)
check('aviso que lanza: el estado cambia igual', m2.revisar() is False)

# ── 5. hay_internet con un puerto cerrado ───────────────────────────────────────
check('localhost:1 cerrado: False', red.hay_internet('127.0.0.1', 1, timeout=1.0) is False)

# ── 6. Hilo ─────────────────────────────────────────────────────────────────────
sondeos = []
listo = threading.Event()


def _sondear():
    sondeos.append(1)
    if len(sondeos) >= 2:
        listo.set()
    return False


m3 = red.MonitorRed(sondear=_sondear, periodo_s=0.05)
m3.iniciar()
check('el hilo sondea solo', listo.wait(2.0) and not m3.en_linea)
m3.cerrar()
time.sleep(0.15)
n = len(sondeos)
time.sleep(0.2)
check('cerrar() detiene el hilo', len(sondeos) == n, f'({n} sondeos)')

print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
