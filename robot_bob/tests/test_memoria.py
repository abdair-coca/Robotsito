"""
test_memoria.py — Prueba aislada del reconocimiento facial + memoria (P1).

Abre la cámara, corre InsightFace y reconoce contra la base SQLite (memory.db).
Sirve para validar que distingue personas ANTES de integrarlo a la conversación.

Controles (con foco en la ventana de video):
  E = enrolar la cara actual (pide el nombre en la consola)
  Q = salir

⚠️ La primera vez InsightFace tarda en cargar (modelos). Esperá a "[face_id] listo".

  python tests/test_memoria.py
"""

import os
import sys
import time
import threading
import cv2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
cv2.setNumThreads(1)

from config import URL_STREAM
from facial_tracker import LectorStream
from face_id import FaceID
from memory import Memoria


def main() -> None:
    print('═' * 56)
    print('  TEST MEMORIA — reconocimiento facial + SQLite')
    print('═' * 56)
    print('  E = enrolar cara actual   Q = salir\n')

    stream = LectorStream(URL_STREAM)
    face   = FaceID()
    mem    = Memoria()
    print(f'  Personas en la base: {mem.total_personas()}')

    # InsightFace corre en un HILO APARTE para no trabar el video (en CPU es
    # pesado). El loop principal solo muestra; el worker analiza el último frame.
    lock     = threading.Lock()
    detener  = threading.Event()
    compart  = {'frame': None, 'label': 'cargando InsightFace...', 'emb': None, 'edad': None}

    def worker():
        while not detener.is_set():
            if not face.listo:
                time.sleep(0.2)
                continue
            with lock:
                f = compart['frame']
            if f is None:
                time.sleep(0.05)
                continue
            emb, edad = face.analizar(f)
            if emb is None:
                lbl = 'sin cara'
            else:
                m = mem.reconocer(emb)
                if m:
                    pid, nombre, score = m
                    mem.marcar_visto(pid)
                    lbl = f'{nombre} (score {score:.2f}, edad~{edad})'
                else:
                    lbl = f'DESCONOCIDO (edad~{edad}) - apreta E'
            with lock:
                compart['label'], compart['emb'], compart['edad'] = lbl, emb, edad
            time.sleep(0.3)

    threading.Thread(target=worker, daemon=True, name='face-id-worker').start()

    try:
        while True:
            frame = stream.leer()
            if frame is None:
                time.sleep(0.02)
                continue

            with lock:
                compart['frame'] = frame            # publicar el último frame
                etiqueta = compart['label']
                emb_actual, edad_actual = compart['emb'], compart['edad']

            color = (0, 255, 0) if ('DESCONOCIDO' not in etiqueta
                                    and 'sin cara' not in etiqueta) else (0, 200, 255)
            cv2.putText(frame, etiqueta, (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            cv2.imshow('TEST Memoria', frame)

            k = cv2.waitKey(1) & 0xFF
            if k == ord('q'):
                break
            elif k == ord('e') and emb_actual is not None:
                nombre = input('Nombre de la persona: ').strip()
                if nombre:
                    pid = mem.registrar(nombre, emb_actual, edad_actual)
                    print(f'  ✓ Enrolado "{nombre}" (id={pid}). Total: {mem.total_personas()}')
    finally:
        detener.set()
        stream.cerrar()
        mem.cerrar()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
