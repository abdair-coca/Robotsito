"""
test_supervisor.py — Supervisor de Bob (supervisor.py), S5 de la Fase S.

Sin Bob de verdad (lanzar devuelve códigos de prueba), salvo el punto 6:
  1. Sale con 0: no relanza.
  2. Sale con 3: relanza enseguida, sin esperar.
  3. Se cae: relanza tras la espera; 3 caídas en 5 min, para con el último código.
  4. Caídas separadas por más de 5 min: sigue relanzando.
  5. Reinicios en bucle: para.
  6. Un proceso real que sale con 3 y después con 0 se lanza dos veces; recibe
     BOB_SUPERVISADO=1 y los argumentos.
  7. Arreglo pendiente (S6): si Bob se cae enseguida, se revierte; si dura o se cierra
     a propósito, queda; una marca rota no tumba al supervisor.

  python tests/unit/test_supervisor.py
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, RAIZ)

import supervisor   # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def correr(codigos, pasos_reloj=0.0, pendiente=None):
    """Supervisa un Bob falso que sale con `codigos` en orden. Devuelve (código final,
    lanzamientos, esperas, avisos)."""
    t = [0.0]
    lanzados, esperas, avisos = [], [], []
    cola = list(codigos)

    def lanzar(args):
        lanzados.append(list(args))
        t[0] += pasos_reloj
        return cola.pop(0)

    final = supervisor.supervisar(['--alone'], lanzar=lanzar, reloj=lambda: t[0],
                                  dormir=esperas.append, avisar=avisos.append,
                                  pendiente=pendiente)
    return final, lanzados, esperas, avisos


# ── 1. Cierre a propósito ───────────────────────────────────────────────────────
final, lanzados, esperas, _ = correr([0])
check('sale con 0: no relanza', final == 0 and len(lanzados) == 1 and esperas == [])
check('pasa los argumentos', lanzados[0] == ['--alone'])

# ── 2. Reinicio pedido ──────────────────────────────────────────────────────────
final, lanzados, esperas, avisos = correr([3, 3, 0])
check('sale con 3: relanza sin esperar', final == 0 and len(lanzados) == 3 and esperas == [])
check('avisa cada reinicio', len(avisos) == 2 and 'reiniciarse' in avisos[0], f'{avisos}')

# ── 3. Caídas ───────────────────────────────────────────────────────────────────
final, lanzados, esperas, avisos = correr([1, 0])
check('se cae una vez: relanza tras la espera',
      final == 0 and len(lanzados) == 2 and esperas == [supervisor.ESPERA_CAIDA_S])
final, lanzados, esperas, avisos = correr([1, 1, 7, 0], pasos_reloj=10.0)
check('3 caídas en 5 min: para con el último código', final == 7 and len(lanzados) == 3,
      f'(final {final}, {len(lanzados)} lanzamientos)')
check('avisa que no lo relanza más', 'no lo relanzo más' in avisos[-1], avisos[-1])
final, lanzados, _, _ = correr([1, 3, 1, 3, 0], pasos_reloj=10.0)
check('los reinicios no cuentan como caídas', final == 0 and len(lanzados) == 5)

# ── 4. Caídas separadas ─────────────────────────────────────────────────────────
final, lanzados, _, _ = correr([1, 1, 1, 1, 0], pasos_reloj=supervisor.VENTANA_S / 2 + 1)
check('caídas separadas por más de 5 min: sigue', final == 0 and len(lanzados) == 5)

# ── 5. Bucle de reinicios ───────────────────────────────────────────────────────
final, lanzados, _, avisos = correr([3] * (supervisor.MAX_REINICIOS + 1) + [0])
check('reinicios en bucle: para', final == 3 and len(lanzados) == supervisor.MAX_REINICIOS + 1
      and 'bucle' in avisos[-1], f'({len(lanzados)} lanzamientos)')

# ── 6. Proceso real ─────────────────────────────────────────────────────────────
tmp = tempfile.mkdtemp()
contador = os.path.join(tmp, 'veces.txt')
falso = os.path.join(tmp, 'main.py')
with open(falso, 'w', encoding='utf-8') as f:
    f.write(textwrap.dedent(f'''
        import os, sys
        ruta = {contador!r}
        n = int(open(ruta).read()) if os.path.exists(ruta) else 0
        open(ruta, 'w').write(str(n + 1))
        print('supervisado' if os.environ.get('BOB_SUPERVISADO') == '1' else 'suelto', sys.argv[1:])
        sys.exit(3 if n == 0 else 0)
    '''))
copia = os.path.join(tmp, 'supervisor.py')
with open(os.path.join(RAIZ, 'supervisor.py'), encoding='utf-8') as f, \
        open(copia, 'w', encoding='utf-8') as g:
    g.write(f.read())
r = subprocess.run([sys.executable, copia, '--alone'], capture_output=True, text=True, timeout=30)
salida = r.stdout.splitlines()
check('proceso real: sale con 0', r.returncode == 0, f'(código {r.returncode}) {r.stderr[-200:]}')
check('proceso real: lanzado dos veces', open(contador).read() == '2')
check('main recibe BOB_SUPERVISADO=1 y los argumentos',
      salida.count("supervisado ['--alone']") == 2, f'{salida}')


# ── 7. Arreglo pendiente ────────────────────────────────────────────────────────
def con_arreglo():
    d = tempfile.mkdtemp()
    editado, nuevo = os.path.join(d, 'modulo.py'), os.path.join(d, 'nuevo.py')
    copia = os.path.join(d, 'copia-modulo.py')
    for ruta, texto in ((editado, 'arreglado'), (nuevo, 'nuevo'), (copia, 'original')):
        with open(ruta, 'w') as f:
            f.write(texto)
    indice = os.path.join(d, 'copias.json')
    with open(indice, 'w') as f:
        json.dump({editado: copia, nuevo: None}, f)
    pend = os.path.join(d, 'pendiente.json')
    with open(pend, 'w') as f:
        json.dump({'carpeta': d, 'copias': indice}, f)
    return pend, editado, nuevo


def leer(ruta):
    with open(ruta) as f:
        return f.read()


pend, editado, nuevo = con_arreglo()
final, lanzados, _, avisos = correr([3, 1, 0], pasos_reloj=5.0, pendiente=pend)
check('se cae enseguida con el arreglo: revierte', leer(editado) == 'original'
      and not os.path.exists(nuevo) and not os.path.exists(pend), f'{avisos}')
check('y lo relanza como en una caída', final == 0 and len(lanzados) == 3
      and any('deshice el arreglo (2 archivo(s))' in a for a in avisos))
pend, editado, nuevo = con_arreglo()
correr([3, 1, 0], pasos_reloj=supervisor.ESTABLE_S + 1, pendiente=pend)
check('se cae después de ESTABLE_S: el arreglo queda', leer(editado) == 'arreglado'
      and os.path.exists(nuevo) and not os.path.exists(pend))
pend, editado, nuevo = con_arreglo()
correr([3, 0], pasos_reloj=5.0, pendiente=pend)
check('se cierra a propósito: el arreglo queda', leer(editado) == 'arreglado'
      and not os.path.exists(pend))
pend, editado, _ = con_arreglo()
with open(pend) as f:
    os.remove(json.load(f)['copias'])
final, _, _, avisos = correr([1, 0], pendiente=pend)
check('marca rota: avisa y no se cae', final == 0 and any('No pude revisar' in a for a in avisos))

print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
sys.exit(1 if fallos else 0)
