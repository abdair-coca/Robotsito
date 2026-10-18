"""
test_escritura_live.py — Claude Code real escribiendo desde el modo ingeniero (fase B3).

Usa una carpeta temporal, no AppInglés. La voz del dueño es simulada: en una corrida
responde "sí" y en la otra "no". Gasta dos turnos de Claude (Haiku, suscripción Pro).
Comprueba: con "sí" el archivo se crea; con "no" no se crea.

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


class SMFalso:
    def iniciar_hablando(self):
        pass


class MemoriaFalsa:
    def total_voces(self, persona_id):
        return 20

    def verificar_voz(self, persona_id, embedding):
        return 0.9               # voz del dueño

    def contexto(self, persona_id):
        return ''


class VPFalso:
    """Voz simulada: graba un trozo cualquiera y transcribe lo que se le diga."""
    def __init__(self, respuesta):
        self.respuesta = respuesta
        self._memoria = MemoriaFalsa()
        self._sm = SMFalso()
        self._modo_ingeniero = True
        self._dueno_turno = True
        self._sesion_ing = None
        self._client = None
        self.hablados = []

    def _grabar(self, **kwargs):
        return b'wav'

    def _analizar_voz(self, wav):
        return [0.1, 0.2], 2.0

    def _transcribir(self, wav, contexto=True):
        return self.respuesta

    def _hablar(self, texto, emo=None):
        self.hablados.append(texto)


def corrida(respuesta: str):
    """Un turno de escritura con la voz `respuesta`. Devuelve (archivo creado, texto, vp)."""
    carpeta = tempfile.mkdtemp(prefix='AppInglesEscLive')
    original = modo_dueno.APPINGLES_DIR
    original_disp = claude_code.disponible
    modo_dueno.APPINGLES_DIR = carpeta
    claude_code.disponible = lambda: True
    sesion = None
    try:
        vp = VPFalso(respuesta)
        ajustes = modo_dueno.ajustes_ingeniero(vp)
        sesion = ajustes['sesion']
        sesion.iniciar()
        texto = sesion.preguntar('Crea un archivo llamado nota.txt con la palabra hola dentro. '
                                 'Usa la herramienta de escritura.',
                                 timeout=modo_dueno.ESCRITURA_TIMEOUT_S)
        creado = os.path.exists(os.path.join(carpeta, 'nota.txt'))
        contenido = ''
        if creado:
            with open(os.path.join(carpeta, 'nota.txt'), encoding='utf-8') as f:
                contenido = f.read()
        return creado, contenido, texto, vp
    finally:
        if sesion is not None:
            sesion.cerrar()
        modo_dueno.APPINGLES_DIR = original
        claude_code.disponible = original_disp
        shutil.rmtree(carpeta, ignore_errors=True)


creado, contenido, texto, vp = corrida('sí')
check("con 'sí' del dueño: el archivo se crea", creado, f"-> {texto}")
check("con 'sí': Bob preguntó antes de escribir",
      any('nota.txt' in h for h in vp.hablados), f"-> {vp.hablados}")
check("con 'sí': el contenido incluye 'hola'", 'hola' in contenido.lower(), f"-> {contenido!r}")

creado, contenido, texto, vp = corrida('no')
check("con 'no' del dueño: el archivo no se crea", not creado, f"-> {texto}")

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}", flush=True)
sys.exit(0 if fallos == 0 else 1)
