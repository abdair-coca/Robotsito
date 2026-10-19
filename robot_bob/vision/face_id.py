"""
face_id.py — Reconocimiento facial (IDENTIDAD) con InsightFace.

La DETECCIÓN de caras para el seguimiento la hace MediaPipe (facial_tracker).
Esto es aparte: da un EMBEDDING (vector de 512 dims, L2-normalizado) de una cara,
que luego se compara contra los guardados para saber QUIÉN es (memory.py).

Carga pesada (modelos buffalo_l ~300 MB) → se inicializa en un hilo de fondo;
`listo` indica cuándo está disponible. El análisis es on-demand (no por frame).
"""

import threading
import numpy as np


class FaceID:
    def __init__(self, det_size=(320, 320)):
        self._app      = None
        self._lock     = threading.Lock()
        self._listo    = threading.Event()
        self._det_size = det_size
        threading.Thread(target=self._cargar, daemon=True, name='face-id-load').start()

    def _cargar(self) -> None:
        try:
            from insightface.app import FaceAnalysis
            app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider'])
            app.prepare(ctx_id=-1, det_size=self._det_size)
            with self._lock:
                self._app = app
            self._listo.set()
            print('[face_id] InsightFace listo (buffalo_l).')
        except Exception as e:
            print(f'[face_id] No se pudo cargar InsightFace: {e}')

    @property
    def listo(self) -> bool:
        return self._listo.is_set()

    def analizar(self, frame_bgr):
        """Devuelve (embedding L2-normalizado np.float32[512], edad:int|None) de la
        cara MÁS GRANDE del frame, o (None, None) si no hay cara / no está listo."""
        app = self._app
        if app is None or frame_bgr is None:
            return None, None
        try:
            with self._lock:
                faces = app.get(frame_bgr)
        except Exception as e:
            print(f'[face_id] error analizando: {e}')
            return None, None
        if not faces:
            return None, None
        f = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        emb  = f.normed_embedding.astype(np.float32)   # ya viene L2-normalizado
        edad = int(f.age) if getattr(f, 'age', None) is not None else None
        return emb, edad
