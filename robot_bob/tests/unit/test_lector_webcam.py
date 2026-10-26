"""
test_lector_webcam.py — La webcam de la laptop (bob --alone), con el mismo contrato que
LectorStream. Sin cámara real: cv2.VideoCapture se cambia por uno falso.

  1. Con la cámara abierta entrega frames, y leer() devuelve una copia.
  2. Si no abre, lo avisa y no queda ok.
  3. cerrar() para el hilo y suelta la cámara.

  python tests/unit/test_lector_webcam.py
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

import cv2                         # noqa: E402
from vision import facial_tracker  # noqa: E402

fallos = 0


def check(nombre: str, ok: bool, detalle: str = "") -> None:
    global fallos
    fallos += 0 if ok else 1
    print(f"[{'OK' if ok else 'FALLA'}] {nombre} {detalle}", flush=True)


class CamaraFalsa:
    def __init__(self, abre: bool):
        self._abre = abre
        self.liberada = False

    def isOpened(self):
        return self._abre

    def read(self):
        time.sleep(0.01)
        return True, np.full((240, 320, 3), 7, np.uint8)

    def release(self):
        self.liberada = True


original_captura = cv2.VideoCapture
original_error = facial_tracker.errores.error
avisos = []

try:
    # ── 1. Con cámara ──────────────────────────────────────────────────────────
    camara = CamaraFalsa(abre=True)
    cv2.VideoCapture = lambda *a, **k: camara
    lector = facial_tracker.LectorWebcam(0)
    time.sleep(0.1)
    frame = lector.leer()
    check('con cámara: entrega frames', frame is not None and frame.shape == (240, 320, 3) and lector.ok)
    check('leer() devuelve una copia', frame is not lector._frame)
    lector.cerrar()
    time.sleep(0.2)
    check('cerrar(): suelta la cámara', camara.liberada)

    # ── 2. Sin cámara ──────────────────────────────────────────────────────────
    facial_tracker.errores.error = lambda *a, **k: avisos.append(a)
    cv2.VideoCapture = lambda *a, **k: CamaraFalsa(abre=False)
    sin_camara = facial_tracker.LectorWebcam(0)
    time.sleep(0.1)
    check('sin cámara: avisa y no queda ok',
          len(avisos) == 1 and not sin_camara.ok and sin_camara.leer() is None, repr(avisos))
finally:
    cv2.VideoCapture = original_captura
    facial_tracker.errores.error = original_error

print(f"\nRESULTADO: {'TODO OK' if fallos == 0 else f'{fallos} FALLO(S)'}")
sys.exit(1 if fallos else 0)
