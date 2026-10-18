"""
test_escritura_live.py — Claude Code real escribiendo desde el modo ingeniero (fase B3, ajustada).

Usa una carpeta temporal, no AppInglés. Sin confirmación por voz: el dueño ya se verificó
al activar el modo. Dos turnos: crear nota.txt y después editarlo. Gasta dos turnos de
Claude (Haiku, suscripción Pro).

  PYTHONIOENCODING=utf-8 python tests/test_escritura_live.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import claude_code   # noqa: E402
import modo_dueno    # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class MemoriaFalsa:
    def contexto(self, persona_id):
        return ''


class VPFalso:
    """Lo mínimo que el modo ingeniero lee del pipeline. Sin micrófono ni voz."""
    def __init__(self):
        self._memoria = MemoriaFalsa()
        self._modo_ingeniero = True
        self._sesion_ing = None
        self._contexto_web = None
        self._client = None


def leer(ruta: str) -> str:
    if not os.path.exists(ruta):
        return ''
    with open(ruta, encoding='utf-8') as f:
        return f.read()


carpeta = tempfile.mkdtemp(prefix='AppInglesEscLive')
original = modo_dueno.APPINGLES_DIR
original_disp = claude_code.disponible
modo_dueno.APPINGLES_DIR = carpeta
claude_code.disponible = lambda: True
sesion = None
try:
    sesion = modo_dueno.ajustes_ingeniero(VPFalso())['sesion']
    ruta = os.path.join(carpeta, 'nota.txt')

    t1 = sesion.preguntar('Crea un archivo llamado nota.txt con la palabra hola dentro.',
                          timeout=60)
    check("crear: sin voz, nota.txt se crea", os.path.exists(ruta), f"-> {t1}")
    check("crear: el contenido incluye 'hola'", 'hola' in leer(ruta).lower(), f"-> {leer(ruta)!r}")

    t2 = sesion.preguntar('Edita nota.txt y cambia la palabra hola por adios.', timeout=60)
    check("editar: cambia el contenido sin voz",
          'adios' in leer(ruta).lower() and 'hola' not in leer(ruta).lower(),
          f"-> {leer(ruta)!r} / {t2}")
finally:
    if sesion is not None:
        sesion.cerrar()
    modo_dueno.APPINGLES_DIR = original
    claude_code.disponible = original_disp
    shutil.rmtree(carpeta, ignore_errors=True)

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
