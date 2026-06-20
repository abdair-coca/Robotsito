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
import cv2

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
cv2.setNumThreads(1)

from config import URL_STREAM, ROTAR_CAMARA_180
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

    ultimo_analisis = 0.0
    etiqueta = 'cargando InsightFace...'
    emb_actual = None
    edad_actual = None

    try:
        while True:
            frame = stream.leer()
            if frame is None:
                time.sleep(0.02)
                continue

            ahora = time.time()
            # Analizar cada 0.5 s (InsightFace es pesado en CPU)
            if face.listo and ahora - ultimo_analisis > 0.5:
                ultimo_analisis = ahora
                emb_actual, edad_actual = face.analizar(frame)
                if emb_actual is None:
                    etiqueta = 'sin cara'
                else:
                    m = mem.reconocer(emb_actual)
                    if m:
                        pid, nombre, score = m
                        mem.marcar_visto(pid)
                        etiqueta = f'{nombre} (score {score:.2f}, edad~{edad_actual})'
                    else:
                        etiqueta = f'DESCONOCIDO (edad~{edad_actual}) - apreta E'

            color = (0, 255, 0) if 'DESCONOCIDO' not in etiqueta and 'sin cara' not in etiqueta else (0, 200, 255)
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
        stream.cerrar()
        mem.cerrar()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
