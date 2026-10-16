"""
test_voz_dueno.py — Aprendizaje de la voz del dueño (V3), sin red, cámara ni micrófono.

Simula la cara reconocida, el modelo de voz y la grabación para comprobar:
la frase "aprende mi voz", las muestras pasivas (solo con la cara del dueño y
frases largas), el rechazo de una voz que contradice a la cara y el flujo
explícito. Usa una memory.db temporal. Si hay grabaciones en exp_voz/, repite
el caso de conflicto con voces reales.

  python tests/test_voz_dueno.py
"""
import glob
import os
import shutil
import sys
import tempfile
import threading

import numpy as np

_BOB = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, _BOB)

import voice_pipeline as vp   # noqa: E402
from memory import Memoria    # noqa: E402

DIR_WAV = os.path.join(_BOB, 'exp_voz')
fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class FakeSM:
    mood = 0.0

    def __init__(self):
        self.ev_hablando = threading.Event()
        self.ev_escuchando = threading.Event()
        self.ev_pensando = threading.Event()
        self.bailando = threading.Event()

    def __getattr__(self, n):
        return lambda *a, **k: None


class FakeMgr:
    def __getattr__(self, n):
        return lambda *a, **k: None


def vec(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(256).astype(np.float32)
    return v / np.linalg.norm(v)


class FakeVoiceID:
    """analizar(audio) → lo que diga el diccionario audio→(emb, segundos)."""
    listo = True

    def __init__(self):
        self.tabla = {}

    def analizar(self, audio):
        return self.tabla.get(audio, (None, 0.0))


def construir(mem, vid, pid=1, score=0.8):
    v = vp.VoicePipeline(FakeMgr(), FakeSM(), face_id=object(), memoria=mem,
                         get_frame=lambda: None, voice_id=vid)
    v._convo_persona, v._convo_face_score = pid, score
    v.dichas = []
    v._hablar = lambda texto, emo=None: v.dichas.append(texto)
    return v


def main() -> None:
    vp.MEMORIA_ENABLED = True
    vp.VOZ_ID_ENABLED = True
    vp.DUENO_ID = 1
    tmp = tempfile.mkdtemp(prefix='bob_voz3_')
    try:
        # 1) La frase del comando
        for t in ('Bob, aprende mi voz', 'apréndete mi voz porfa', 'memoriza bien mi voz',
                  'guarda mi voz'):
            check(f'comando: "{t}"', vp._pide_aprender_voz(t))
        for t in ('mi voz suena rara', 'aprende a bailar', 'pon música', ''):
            check(f'no comando: "{t}"', not vp._pide_aprender_voz(t))

        mem = Memoria(os.path.join(tmp, 'm.db'))
        dueno = mem.registrar('Abdair', None)
        vid = FakeVoiceID()

        # 2) Apagado / sin modelo → todo igual que antes
        v = construir(mem, vid)
        vp.VOZ_ID_ENABLED = False
        check('VOZ_ID_ENABLED=False → inactiva', not v._voz_activa())
        vp.VOZ_ID_ENABLED = True
        check('VOZ_ID_ENABLED=True → activa', v._voz_activa())
        check('sin voice_id → inactiva', not construir(mem, None)._voz_activa())

        # 3) ¿Es el dueño por la cara?
        check('dueño con score alto', construir(mem, vid, dueno, 0.8)._dueno_por_cara())
        check('dueño con score bajo → no', not construir(mem, vid, dueno, 0.4)._dueno_por_cara())
        check('otra persona → no', not construir(mem, vid, 99, 0.9)._dueno_por_cara())
        check('desconocido → no', not construir(mem, vid, None, None)._dueno_por_cara())

        # 4) Muestras pasivas
        base = vec(1)
        for i in range(6):
            r = base + vec(10 + i) * 0.3
            vid.tabla[f'larga{i}'.encode()] = (r / np.linalg.norm(r), 2.5)
        vid.tabla[b'corta'] = (base, 1.2)
        vid.tabla[b'ajena'] = (vec(2), 3.0)
        v = construir(mem, vid)
        check('frase corta no se guarda', not v._muestra_voz(b'corta', 'pasivo'))
        check('sin audio no se guarda', not v._muestra_voz(None, 'pasivo'))
        for i in range(5):
            v._muestra_voz(f'larga{i}'.encode(), 'pasivo')
        check('5 muestras largas guardadas', mem.total_voces(dueno) == 5)
        check('voz que contradice a la cara no se guarda', not v._muestra_voz(b'ajena', 'pasivo'))
        check('voz que coincide se guarda', v._muestra_voz(b'larga5', 'pasivo'))

        # 5) "Aprende mi voz" sin la cara del dueño → se niega y no graba
        v = construir(mem, vid, 99, 0.9)
        grabadas = []
        v._grabar = lambda **k: grabadas.append(1) or b'larga0'
        antes = mem.total_voces(dueno)
        v._aprender_voz()
        check('sin cara del dueño: no graba', not grabadas and mem.total_voces(dueno) == antes,
              f'-> {v.dichas}')

        # 6) Con la cara del dueño: una pregunta por muestra, todas explícitas
        mem2 = Memoria(os.path.join(tmp, 'm2.db'))
        d2 = mem2.registrar('Abdair', None)
        v = construir(mem2, vid, d2, 0.8)
        cola = [f'larga{i}'.encode() for i in range(5)]
        v._grabar = lambda **k: cola.pop(0) if cola else None
        v._aprender_voz()
        with mem2._lock:
            origenes = [o for (o,) in mem2._con.execute('SELECT origen FROM voces')]
        check('aprende mi voz: 5 muestras explícitas', origenes == ['explicito'] * 5,
              f'-> {origenes}')
        check('aprende mi voz: hizo las 5 preguntas',
              all(p in v.dichas for p in vp.PREGUNTAS_VOZ))
        check('aprende mi voz: avisa cuántas guardó', any('5 frases' in d for d in v.dichas))

        # 7) Si no contesta, corta y no se cuelga
        mem3 = Memoria(os.path.join(tmp, 'm3.db'))
        d3 = mem3.registrar('Abdair', None)
        v = construir(mem3, vid, d3, 0.8)
        v._grabar = lambda **k: None
        v._aprender_voz()
        check('sin respuesta: termina sin muestras', mem3.total_voces(d3) == 0
              and any('No pude' in d for d in v.dichas))
        for m in (mem, mem2, mem3):
            m.cerrar()

        # 8) Voces reales: tus frases se guardan, las de otra persona no
        rutas = sorted(glob.glob(os.path.join(DIR_WAV, '*.wav')))
        abd = [r for r in rutas if os.path.basename(r).startswith('abdair_')]
        ajenas = [r for r in rutas if r not in abd]
        if len(abd) > 5 and ajenas:
            from voice_id import VoiceID
            mem4 = Memoria(os.path.join(tmp, 'm4.db'))
            d4 = mem4.registrar('Abdair', None)
            v = construir(mem4, VoiceID(en_fondo=False), d4, 0.8)
            leer = lambda r: open(r, 'rb').read()   # noqa: E731
            guardadas = sum(v._muestra_voz(leer(r), 'pasivo') for r in abd)
            rechazadas = sum(not v._muestra_voz(leer(r), 'pasivo') for r in ajenas)
            check('real: frases de Abdair guardadas (≥ 2 s de voz)', guardadas >= 10,
                  f'({guardadas}/{len(abd)})')
            check('real: frases ajenas con tu cara delante, rechazadas',
                  rechazadas == len(ajenas), f'({rechazadas}/{len(ajenas)})')
            mem4.cerrar()
        else:
            print('[--] faltan grabaciones en exp_voz/: caso real no ejecutado')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} FALLA(S)'}")
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
