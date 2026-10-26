"""
test_serial_simulado.py — El sustituto de SerialManager para `bob --alone` (sin ESP32).

Sin hardware ni red:
  1. Tiene la misma API que SerialManager (lo que usa el resto de Bob).
  2. Guarda la última posición, con el mismo recorte que el DevKit, y la cara del OLED.
  3. estado_cli.foto() muestra la cabeza y la cara solo cuando hay algo que mostrar.

  python tests/unit/test_serial_simulado.py
"""
import os
import sys
import threading
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from hardware.serial_manager import SerialManager    # noqa: E402
from hardware.serial_simulado import SerialSimulado  # noqa: E402
from nucleo import estado_cli                         # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


# ── 1. Misma API ───────────────────────────────────────────────────────────────
publicos = [m for m in dir(SerialManager) if m.startswith('cmd_') or m == 'cerrar']
check('misma API que SerialManager', all(hasattr(SerialSimulado, m) for m in publicos), repr(publicos))

# ── 2. Posición y cara ─────────────────────────────────────────────────────────
s = SerialSimulado()
check('al arrancar no hay posición ni cara', s.posicion is None and s._ultimo_estado == '')
s.cmd_servo(92.7, 81.2)
check('servo: grados enteros, como el DevKit', s.posicion == (92, 81), repr(s.posicion))
s.cmd_servo(-5, 400)
check('servo: recorta a 0..180', s.posicion == (0, 180), repr(s.posicion))
s.cmd_estado('FELIZ')
check('estado: la cara del OLED queda guardada', s._ultimo_estado == 'FELIZ')
s.cmd_motor(50, -50)
s.cmd_siguiendo(0.2, -0.1)
check('motores y mirada: se aceptan sin efecto', s.posicion == (0, 180) and s._ultimo_estado == 'FELIZ')
s.cerrar()

# ── 3. estado_cli.foto() ───────────────────────────────────────────────────────
sm = NS(estado=NS(name='IDLE'), musica_sonando=threading.Event(), bailando=threading.Event())
voice = NS(microfono_activo=True, _modo_ingeniero=False)
check('foto: sin posición no hay cabeza', 'cabeza' not in estado_cli.foto(sm, voice, NS(_ultimo_estado='')))
f = estado_cli.foto(sm, voice, s)
check('foto: cabeza y cara del serial simulado', f.get('cabeza') == {'pan': 0, 'tilt': 180}
      and f.get('emocion') == 'FELIZ', repr(f))

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLO(S)'}")
sys.exit(1 if fallos else 0)
