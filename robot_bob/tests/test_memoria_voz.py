"""
test_memoria_voz.py — Muestras de voz en Memoria (V2), sin red, cámara ni micrófono.

Trabaja SOLO sobre bases temporales: la migración se prueba con una COPIA de
memory.db (si existe); la base real no se abre. Si hay grabaciones del
experimento en exp_voz/, enrola 5 frases de Abdair y verifica el resto.

  python tests/test_memoria_voz.py
"""
import glob
import os
import shutil
import sqlite3
import sys
import tempfile

import numpy as np

_BOB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _BOB)

from memoria import memory   # noqa: E402
from memoria.memory import Memoria   # noqa: E402

DB_REAL = os.path.join(_BOB, 'memory.db')
DIR_WAV = os.path.join(_BOB, 'exp_voz')
fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


def vec(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(256).astype(np.float32)
    return v / np.linalg.norm(v)


def conteos(ruta: str) -> dict:
    con = sqlite3.connect(ruta)
    try:
        return {t: con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                for t in ('personas', 'episodios')}
    finally:
        con.close()


def main() -> None:
    tmp = tempfile.mkdtemp(prefix='bob_voz_')
    try:
        # ── Base nueva ──────────────────────────────────────────────────────
        m = Memoria(os.path.join(tmp, 'nueva.db'))
        pid = m.registrar('Abdair', None)
        check('sin muestras → centroide None', m.centroide_voz(pid) is None)
        check('sin muestras → verificar None', m.verificar_voz(pid, vec(1)) is None)
        m.agregar_voz(pid, None)
        check('embedding None no se guarda', m.total_voces(pid) == 0)

        base = vec(1)
        for i in range(5):
            ruido = vec(100 + i) * 0.3
            m.agregar_voz(pid, (base + ruido) / np.linalg.norm(base + ruido))
        check('5 muestras guardadas', m.total_voces(pid) == 5)
        c = m.centroide_voz(pid)
        check('centroide normalizado', abs(np.linalg.norm(c) - 1) < 1e-4)
        s_mismo, s_otro = m.verificar_voz(pid, base), m.verificar_voz(pid, vec(2))
        check('misma voz > otra voz', s_mismo > s_otro, f'({s_mismo:.3f} vs {s_otro:.3f})')

        # Tope: las explícitas sobreviven, las pasivas más viejas se van.
        otro = m.registrar('Otro', None)
        for i in range(3):
            m.agregar_voz(otro, vec(200 + i), origen='explicito')
        for i in range(memory.MAX_VOCES + 5):
            m.agregar_voz(otro, vec(300 + i))
        with m._lock:
            origenes = [o for (o,) in m._con.execute(
                'SELECT origen FROM voces WHERE persona_id=?', (otro,))]
        check('tope MAX_VOCES', len(origenes) == memory.MAX_VOCES, f'({len(origenes)})')
        check('explícitas conservadas', origenes.count('explicito') == 3)
        check('no toca las voces de otra persona', m.total_voces(pid) == 5)
        m.cerrar()

        # ── Migración sobre una copia de la base real ──────────────────────
        if os.path.exists(DB_REAL):
            copia = os.path.join(tmp, 'copia.db')
            shutil.copy2(DB_REAL, copia)
            antes = conteos(copia)
            mc = Memoria(copia)
            mc.cerrar()
            despues = conteos(copia)
            con = sqlite3.connect(copia)
            tablas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            con.close()
            check('migración: tabla voces creada', 'voces' in tablas)
            check('migración: sin perder filas', antes == despues, f'({antes})')
            mc = Memoria(copia)          # abrir dos veces no rompe
            mc.cerrar()
            check('migración: reabrir', True)
        else:
            print('[--] no hay memory.db: migración de base vieja no ejecutada')

        # ── Con grabaciones reales ─────────────────────────────────────────
        rutas = sorted(glob.glob(os.path.join(DIR_WAV, '*.wav')))
        abd = [r for r in rutas if os.path.basename(r).startswith('abdair_')]
        ajenas = [r for r in rutas if r not in abd]
        if len(abd) > 5 and ajenas:
            from voz.voice_id import VoiceID
            vid = VoiceID(en_fondo=False)

            def emb(r):
                with open(r, 'rb') as f:
                    return vid.analizar(f.read())[0]

            m = Memoria(os.path.join(tmp, 'real.db'))
            pid = m.registrar('Abdair', None)
            for r in abd[:5]:
                m.agregar_voz(pid, emb(r), origen='explicito')
            mios = [m.verificar_voz(pid, e) for e in map(emb, abd[5:]) if e is not None]
            otros = [m.verificar_voz(pid, e) for e in map(emb, ajenas) if e is not None]
            check('grabaciones: Abdair por encima de las otras voces', min(mios) > max(otros),
                  f'(Abdair mín {min(mios):.3f}, otras máx {max(otros):.3f})')
            m.cerrar()
        else:
            print('[--] faltan grabaciones en exp_voz/: verificación real no ejecutada')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
