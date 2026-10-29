"""
test_autoarreglo.py — Bob arregla su código (agente/autoarreglo.py), S6 de la Fase S.

Sin Claude de verdad: una sesión falsa edita archivos pasando por el aprobador real, en
una carpeta temporal con un módulo y su test (compilar, importar y correr tests sí son
de verdad):
  1. Zonas prohibidas, firma del error y tests relacionados.
  2. Arreglo bueno: queda, deja informe y marca, pide el reinicio y avisa.
  3. Arreglo que rompe un test que pasaba: se revierte y no reinicia.
  4. Arreglo que no compila, o que crea un archivo: se revierte (el nuevo se borra).
  5. Zona prohibida y herramienta no permitida: se niegan y no se tocan.
  6. Sin modo ingeniero o con el tope del día: solo propuesta, sin editar.
  7. Sin supervisor: aplica pero no reinicia.
  8. El mismo error una sola vez; el hook de hilos lo dispara y sigue imprimiendo.

  python tests/unit/test_autoarreglo.py
"""
import json
import os
import sys
import tempfile
import textwrap
import threading
import time

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, RAIZ)

from agente import autoarreglo   # noqa: E402
from agente.autoarreglo import Autoarreglo   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


ORIGINAL = textwrap.dedent('''
    def suma(a, b):
        return a + b


    def promedio(xs):
        return sum(xs) / len(xs)
''')
ARREGLADO = ORIGINAL.replace('return sum(xs) / len(xs)', 'return sum(xs) / len(xs) if xs else 0.0')
ROMPE = ORIGINAL.replace('return a + b', 'return a - b')
NO_COMPILA = ORIGINAL.replace('return a + b', 'return a +')
TEST = textwrap.dedent('''
    import sys, os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from paquete import calculo
    sys.exit(0 if calculo.suma(1, 2) == 3 else 1)
''')


def carpeta():
    raiz = tempfile.mkdtemp()
    os.makedirs(os.path.join(raiz, 'paquete'))
    os.makedirs(os.path.join(raiz, 'tests', 'unit'))
    open(os.path.join(raiz, 'paquete', '__init__.py'), 'w').close()
    with open(os.path.join(raiz, 'paquete', 'calculo.py'), 'w', encoding='utf-8') as f:
        f.write(ORIGINAL)
    with open(os.path.join(raiz, 'tests', 'unit', 'test_calculo.py'), 'w', encoding='utf-8') as f:
        f.write(TEST)
    with open(os.path.join(raiz, 'tests', 'unit', 'test_otro.py'), 'w', encoding='utf-8') as f:
        f.write('import sys\nsys.exit(0)\n')
    with open(os.path.join(raiz, 'config.py'), 'w', encoding='utf-8') as f:
        f.write('X = 1\n')
    return raiz


class SesionFalsa:
    """Hace lo que diga `ediciones`: [(herramienta, ruta, contenido)]; anota lo que pasó."""
    creadas = []

    def __init__(self, ediciones, prompt, edicion, raiz, aprobar):
        self.ediciones, self.edicion, self.aprobar = ediciones, edicion, aprobar
        self.respuestas = []
        self.cerrada = False
        SesionFalsa.creadas.append(self)

    def preguntar(self, pedido, timeout, max_herramientas):
        self.pedido = pedido
        for herramienta, ruta, contenido in self.ediciones:
            r = self.aprobar(herramienta, {'file_path': ruta}) if self.aprobar else False
            self.respuestas.append(r)
            if r is True:
                with open(ruta, 'w', encoding='utf-8') as f:
                    f.write(contenido)
        return 'calculo.py\nlista vacía\nse devuelve 0.0'

    def cerrar(self):
        self.cerrada = True


def armar(raiz, ediciones, ingeniero=True, supervisado=True):
    avisos, reinicios = [], []
    a = Autoarreglo(en_ingeniero=lambda: ingeniero,
                    crear_sesion=lambda **kw: SesionFalsa(ediciones, **kw),
                    avisar=avisos.append, pedir_reinicio=lambda: reinicios.append(1),
                    raiz=raiz, supervisado=supervisado, lanzar=lambda f: f())
    return a, avisos, reinicios


def leer(ruta):
    with open(ruta, encoding='utf-8') as f:
        return f.read()


def error_de_promedio():
    try:
        sum([]) / len([])
    except ZeroDivisionError as e:
        return e, e.__traceback__


# ── 1. Piezas ───────────────────────────────────────────────────────────────────
r = carpeta()
check('prohibido: .env', autoarreglo.prohibido(os.path.join(r, '.env'), r) is not None)
check('prohibido: .env.local', autoarreglo.prohibido(os.path.join(r, 'x', '.env.local'), r) is not None)
check('prohibido: memory.db', autoarreglo.prohibido(os.path.join(r, 'memory.db'), r) is not None)
check('prohibido: config.py', autoarreglo.prohibido(os.path.join(r, 'config.py'), r) is not None)
check('prohibido: devices.json', autoarreglo.prohibido(os.path.join(r, 'devices.json'), r) is not None)
check('prohibido: fuera de la carpeta',
      autoarreglo.prohibido(os.path.join(os.path.dirname(r), 'firmware', 'main.py'), r) is not None)
check('permitido: un módulo', autoarreglo.prohibido(os.path.join(r, 'paquete', 'calculo.py'), r) is None)
check('permitido: config.py de otra carpeta',
      autoarreglo.prohibido(os.path.join(r, 'paquete', 'config.py'), r) is None)
e, tb = error_de_promedio()
f = autoarreglo.firma(e, tb, RAIZ)
check('firma: tipo y lugar', f.startswith('ZeroDivisionError@tests') and ':' in f, f)
calc = os.path.join(r, 'paquete', 'calculo.py')
rel = [os.path.basename(t) for t in autoarreglo.tests_relacionados([calc], r)]
check('tests relacionados: solo el que importa el módulo', rel == ['test_calculo.py'], f'{rel}')
check('módulo de un __init__', autoarreglo.modulo_de(os.path.join(r, 'paquete', '__init__.py'), r)
      == 'paquete')

# ── 2. Arreglo bueno ────────────────────────────────────────────────────────────
a, avisos, reinicios = armar(r, [('Edit', calc, ARREGLADO)])
res = a.arreglar('ZeroDivisionError: division by zero', 'ZeroDivisionError@paquete/calculo.py:7',
                 'wake-monitor')
check('arreglo bueno: listo', res == 'listo', res)
check('el archivo queda arreglado', leer(calc) == ARREGLADO)
check('pide el reinicio', a.reinicio_pedido and reinicios == [1])
check('avisa empiezo y listo', avisos == ['empiezo', 'listo'], f'{avisos}')
arreglos = os.path.join(r, 'logs', 'arreglos')
pend = json.load(open(os.path.join(arreglos, 'pendiente.json'), encoding='utf-8'))
informe = leer(os.path.join(pend['carpeta'], 'informe.md'))
check('informe con error, diff y tests', 'ZeroDivisionError' in informe and '+    return sum(xs)'
      in informe and 'test_calculo.py`: antes OK, después OK' in informe)
copias = json.load(open(pend['copias'], encoding='utf-8'))
check('copias del original en disco', leer(copias[os.path.realpath(calc)]) == ORIGINAL)
check('sesión cerrada y con el error en el pedido',
      SesionFalsa.creadas[-1].cerrada and 'ZeroDivisionError' in SesionFalsa.creadas[-1].pedido)

# ── 3. Rompe un test que pasaba ─────────────────────────────────────────────────
r = carpeta()
calc = os.path.join(r, 'paquete', 'calculo.py')
a, avisos, reinicios = armar(r, [('Edit', calc, ROMPE)])
res = a.arreglar('Error', 'E@x', 'h')
check('rompe un test: revertido', res == 'revertido' and leer(calc) == ORIGINAL, res)
check('no reinicia y avisa revertido', not a.reinicio_pedido and reinicios == []
      and avisos[-1] == 'revertido')
check('sin marca para el supervisor', not os.path.exists(os.path.join(r, 'logs', 'arreglos',
                                                                      'pendiente.json')))

# ── 4. No compila / archivo nuevo ───────────────────────────────────────────────
nuevo = os.path.join(r, 'paquete', 'ayuda.py')
a, _, _ = armar(r, [('Write', nuevo, 'X = 1\n'), ('Edit', calc, NO_COMPILA)])
res = a.arreglar('Error', 'E@y', 'h')
check('no compila: revertido', res == 'revertido' and leer(calc) == ORIGINAL, res)
check('el archivo nuevo se borra', not os.path.exists(nuevo))

# ── 5. Prohibidos ───────────────────────────────────────────────────────────────
conf = os.path.join(r, 'config.py')
a, _, _ = armar(r, [('Edit', conf, 'X = 2\n'), ('Bash', conf, '')])
res = a.arreglar('Error', 'E@z', 'h')
resp = SesionFalsa.creadas[-1].respuestas
check('config.py: negado y sin tocar', isinstance(resp[0], str) and leer(conf) == 'X = 1\n', f'{resp}')
check('Bash: negado', isinstance(resp[1], str))
check('sin cambios: no reinicia', res == 'sin_cambios' and not a.reinicio_pedido, res)

# ── 6. Solo propuesta ───────────────────────────────────────────────────────────
a, avisos, _ = armar(r, [('Edit', calc, ARREGLADO)], ingeniero=False)
res = a.arreglar('Error', 'E@w', 'h')
s = SesionFalsa.creadas[-1]
check('sin modo ingeniero: propuesta sin editar',
      res == 'propuesta' and not s.edicion and s.aprobar is None and leer(calc) == ORIGINAL, res)
check('avisa propuesta', avisos == ['propuesta'], f'{avisos}')
hoy = time.strftime('%Y-%m-%d')
with open(os.path.join(r, 'logs', 'arreglos', 'registro.jsonl'), 'a', encoding='utf-8') as fh:
    for _ in range(autoarreglo.MAX_POR_DIA):
        fh.write(json.dumps({'fecha': hoy, 'resultado': 'listo'}) + '\n')
a, _, _ = armar(r, [('Edit', calc, ARREGLADO)])
res = a.arreglar('Error', 'E@v', 'h')
check(f'tope de {autoarreglo.MAX_POR_DIA} por día: propuesta', res == 'propuesta'
      and leer(calc) == ORIGINAL, res)

# ── 7. Sin supervisor ───────────────────────────────────────────────────────────
r = carpeta()
calc = os.path.join(r, 'paquete', 'calculo.py')
a, _, reinicios = armar(r, [('Edit', calc, ARREGLADO)], supervisado=False)
res = a.arreglar('Error', 'E@u', 'h')
check('sin supervisor: aplica pero no reinicia', res == 'listo' and leer(calc) == ARREGLADO
      and not a.reinicio_pedido and reinicios == [])

# ── 8. Disparo ──────────────────────────────────────────────────────────────────
r = carpeta()
llamados = []
a = Autoarreglo(en_ingeniero=lambda: False, raiz=r, lanzar=lambda f: llamados.append(f))
e, tb = error_de_promedio()
check('primer error: dispara', a.al_error(e, tb, 'h') and len(llamados) == 1)
check('el mismo error otra vez: no', not a.al_error(e, tb, 'h') and len(llamados) == 1)
try:
    raise KeyError('x')
except KeyError as k:
    otro = k
check('mientras arregla otro: no', not a.al_error(otro, otro.__traceback__, 'h'))

previos = []
hook_original = threading.excepthook
threading.excepthook = lambda args: previos.append(args.exc_type)
disparos = []
b = Autoarreglo(en_ingeniero=lambda: False, raiz=r, lanzar=lambda f: disparos.append(f))
b.instalar()
try:
    t = threading.Thread(target=lambda: 1 / 0, name='hilo-que-falla')
    t.start()
    t.join()
finally:
    threading.excepthook = hook_original
check('hook: imprime como antes y dispara', previos == [ZeroDivisionError] and len(disparos) == 1)

print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
