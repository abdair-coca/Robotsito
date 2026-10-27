"""
test_persona_fija.py — Con varias caras delante (multitud en la feria) el tracker sigue a
UNA persona en vez de saltar a la cara más grande en cada detección. Sin cámara: el
detector de MediaPipe se cambia por uno falso y el reloj lo maneja el test.

  1. Una cara sola: la sigue y apunta a la nariz.
  2. Dos caras parecidas: no salta a la otra cuando crece un poco.
  3. Si la suya se pierde, espera quieta (None) y a los 0.5 s pasa a la otra.
  4. Otra cara el doble de grande le quita el lugar solo tras 1 s seguido.
  5. Si camina, la sigue aunque haya otra cara cerca.
  6. Al cambiar de persona no arrastra la velocidad de la anterior.

  python tests/unit/test_persona_fija.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from vision import facial_tracker  # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class DetectorFalso:
    def __init__(self):
        self.caras = []

    def detect(self, _imagen):
        return SimpleNamespace(detections=self.caras)


def cara(cx, cy, w, nariz=None):
    """Detección cuadrada de lado w centrada en (cx, cy), en píxeles de DET_W x DET_H."""
    kps = None
    if nariz:
        punto = SimpleNamespace(x=nariz[0] / facial_tracker.DET_W, y=nariz[1] / facial_tracker.DET_H)
        kps = [SimpleNamespace(x=0.0, y=0.0)] * 2 + [punto]
    caja = SimpleNamespace(origin_x=cx - w // 2, origin_y=cy - w // 2, width=w, height=w)
    return SimpleNamespace(bounding_box=caja, keypoints=kps)


reloj = [0.0]
FRAME = np.zeros((facial_tracker.DET_H, facial_tracker.DET_W, 3), np.uint8)
abiertos = []


def nuevo():
    falso = DetectorFalso()
    det = facial_tracker.DetectorFacial(falso, facial_tracker.DET_W, facial_tracker.DET_H)
    abiertos.append(det)
    return falso, det


def paso(falso, det, t, *caras):
    """Detección en el instante t con esas caras; devuelve (cx, cy, x, y, w, h) o None."""
    reloj[0] = t
    falso.caras = list(caras)
    return det._detectar(FRAME)


def centro(res):
    """Centro del bbox elegido (sin la proyección del predictor)."""
    return None if res is None else (res[2] + res[4] // 2, res[3] + res[5] // 2)


original_time = facial_tracker.time
facial_tracker.time = SimpleNamespace(monotonic=lambda: reloj[0])
try:
    # ── 1. Una cara sola ───────────────────────────────────────────────────────
    falso, det = nuevo()
    r = paso(falso, det, 0.0, cara(100, 120, 60, nariz=(104, 110)))
    check('una cara: la sigue y apunta a la nariz',
          centro(r) == (100, 120) and r[:2] == (104, 110), repr(r))
    check('sin caras: None', paso(falso, det, 0.1) is None)

    # ── 2. Dos caras parecidas ────────────────────────────────────────────────
    falso, det = nuevo()
    A, B = (80, 120), (240, 120)
    r = paso(falso, det, 0.0, cara(*A, 60), cara(*B, 56))
    check('dos caras: empieza por la más grande', centro(r) == A, repr(r))
    r = paso(falso, det, 0.1, cara(*A, 56), cara(*B, 62))
    check('la otra crece un poco: no salta', centro(r) == A, repr(r))

    # ── 3. La suya se pierde ──────────────────────────────────────────────────
    r = paso(falso, det, 0.3, cara(*B, 62))
    check('la suya no aparece: espera quieta', r is None, repr(r))
    r = paso(falso, det, 0.55, cara(*B, 62))
    check('a los 0.45 s sigue esperando', r is None, repr(r))
    r = paso(falso, det, 0.65, cara(*B, 62))
    check('a los 0.55 s pasa a la otra', centro(r) == B, repr(r))

    # ── 4. Otra mucho más cerca ───────────────────────────────────────────────
    falso, det = nuevo()
    A, C = (100, 120), (230, 110)
    paso(falso, det, 0.0, cara(*A, 50))
    sigue = [centro(paso(falso, det, i / 10, cara(*A, 50), cara(*C, 80))) for i in range(1, 11)]
    check('otra el doble de grande: 0.9 s después sigue con la suya',
          all(c == A for c in sigue), repr(sigue))
    r = paso(falso, det, 1.15, cara(*A, 50), cara(*C, 80))
    check('...pasado 1 s seguido, pasa a la que se acercó', centro(r) == C, repr(r))

    falso, det = nuevo()
    paso(falso, det, 0.0, cara(*A, 50))
    res = []
    for i in range(1, 16):                       # 0.1 .. 1.5 s; a los 0.7 s la otra se va
        caras = [cara(*A, 50)] + ([] if i == 7 else [cara(*C, 80)])
        res.append(centro(paso(falso, det, i / 10, *caras)))
    check('alguien que se acerca y se va no le quita el lugar', all(c == A for c in res), repr(res))

    # ── 5. Camina ─────────────────────────────────────────────────────────────
    falso, det = nuevo()
    res = [centro(paso(falso, det, i / 10, cara(60 + 40 * i, 120, 60), cara(260, 120, 62 if i else 58)))
           for i in range(5)]
    check('camina: la sigue aunque haya otra cara cerca',
          res == [(60 + 40 * i, 120) for i in range(5)], repr(res))

    # ── 6. Predictor al cambiar de persona ────────────────────────────────────
    falso, det = nuevo()
    C = (250, 100)
    antes = None
    for i in range(12):                          # 0.0 .. 1.1 s; la que se acerca, desde 0.1
        caras = [cara(40 + 10 * i, 150, 50)] + ([cara(*C, 80)] if i else [])
        r = paso(falso, det, i / 10, *caras)
        if centro(r) != C:
            antes = r
    check('siguiendo a alguien que camina, el predictor adelanta',
          antes is not None and antes[0] > centro(antes)[0], repr(antes))
    check('al pasar a la que se acercó no arrastra la velocidad de la anterior',
          centro(r) == C and r[:2] == C, repr(r))
finally:
    facial_tracker.time = original_time
    for det in abiertos:
        det.cerrar()

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLO(S)'}")
sys.exit(1 if fallos else 0)
