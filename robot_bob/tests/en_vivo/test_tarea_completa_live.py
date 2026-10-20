"""
test_tarea_completa_live.py — Tarea completa del modo ingeniero con Claude Code real (fase B6).

Una sola orden: buscar una función con un error, leerla, corregirla (sin confirmación, B-D8)
y correr el programa para comprobarlo (Bash, B-D9). Usa una carpeta temporal, no AppInglés.
Mide en disco el resultado (el archivo quedó corregido), las herramientas que usó Claude y
lo que Bob diría en voz alta (B5). Gasta un turno largo de Claude (Haiku, suscripción Pro).

  PYTHONIOENCODING=utf-8 python tests/en_vivo/test_tarea_completa_live.py
"""
import os
import shutil
import sys
import tempfile

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


PROGRAMA = '''def suma(a, b):
    return a - b


if __name__ == "__main__":
    print("2 + 3 =", suma(2, 3))
'''

carpeta = tempfile.mkdtemp(prefix='AppInglesTarea')
os.makedirs(os.path.join(carpeta, 'src'))
ruta_programa = os.path.join(carpeta, 'src', 'calculadora.py')
with open(ruta_programa, 'w', encoding='utf-8') as f:
    f.write(PROGRAMA)
with open(os.path.join(carpeta, 'notas.txt'), 'w', encoding='utf-8') as f:
    f.write('apuntes sueltos, nada que ver con la tarea\n')

original = modo_dueno.APPINGLES_DIR
original_disp = claude_code.disponible
modo_dueno.APPINGLES_DIR = carpeta
claude_code.disponible = lambda: True
decisiones, usos = [], []
sesion = None
try:
    sesion = modo_dueno.ajustes_ingeniero(VPFalso())['sesion']
    decidir_original = sesion.aprobar

    def registrar(herramienta, entrada):
        ok = decidir_original(herramienta, entrada)
        decisiones.append((herramienta, entrada.get('command') or entrada.get('file_path') or '', ok))
        return ok
    sesion.aprobar = registrar

    respuesta = sesion.preguntar(
        'En la carpeta hay un programa de Python cuya función suma da mal el resultado. '
        'Búscalo, léelo, corrige el error y corre el programa para comprobar que 2 + 3 da 5. '
        'Dime en pocas palabras qué hiciste.',
        timeout=180, al_usar=lambda nombre, entrada: usos.append(nombre))

    with open(ruta_programa, encoding='utf-8') as f:
        despues = f.read()
    nombres = set(usos)
    check("buscar: usó Glob o Grep", bool(nombres & {'Glob', 'Grep'}), f"-> {usos}")
    check("leer: usó Read", 'Read' in nombres, f"-> {usos}")
    check("editar: el archivo quedó corregido en disco",
          'a + b' in despues and 'a - b' not in despues, f"-> {despues!r}")
    check("editar: sin confirmación, el host dejó pasar Edit o Write",
          any(h in ('Edit', 'Write') and ok for h, _, ok in decisiones), f"-> {decisiones}")
    check("correr: Bash pasó por el host y se permitió",
          any(h == 'Bash' and ok for h, _, ok in decisiones), f"-> {decisiones}")
    check("ningún permiso negado en la tarea normal", all(ok for _, _, ok in decisiones))

    dichas = modo_dueno.frases_para_decir(respuesta or '', modo_dueno.ING_MAX_FRASES)
    check("voz: Claude respondió y Bob tiene qué decir", bool(dichas), f"-> {respuesta!r}")
    check("voz: hasta 4 frases, ninguna pasa del tope (B5)",
          0 < len(dichas) <= modo_dueno.ING_MAX_FRASES
          and all(len(x) <= modo_dueno.FRASE_MAX_CHARS for x in dichas), f"-> {len(dichas)} frases")
    print("--- Bob diría:", flush=True)
    for x in dichas:
        print(f"  {x}", flush=True)
finally:
    if sesion is not None:
        sesion.cerrar()
    modo_dueno.APPINGLES_DIR = original
    claude_code.disponible = original_disp
    shutil.rmtree(carpeta, ignore_errors=True)

print("--- herramientas usadas:", usos, flush=True)
print("--- decisiones del host:", flush=True)
for h, c, ok in decisiones:
    print(f"  {h} {c[:80]!r}: {'sí' if ok else 'no'}", flush=True)
print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
