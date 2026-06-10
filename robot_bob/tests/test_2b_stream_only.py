"""
test_2b_stream_only.py — Diagnóstico del stream MJPEG sin nada más.

Sirve para aislar si el problema "se cuelga cuando hay cara" es:
  (a) Del ESP32-CAM (envía mal datos por si solo)
  (b) De nuestra app (saturación de GIL/CPU por la detección+dibujo)

Este test SOLO lee el stream y muestra el frame crudo. NO hace:
  - Detección facial (sin MediaPipe)
  - Comandos al ESP32 (sin SerialManager)
  - State machine
  - HUD elaborado

Si este test NO se cuelga (sin reconexiones), el problema es la carga de CPU
del test 2 normal y hay que optimizar.
Si este test SÍ se cuelga, el problema es del ESP32-CAM.

Ejecutar:
  python tests/test_2b_stream_only.py
"""

import os
import sys
import time
import cv2

cv2.setNumThreads(1)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from facial_tracker import LectorStream

URL_STREAM = 'http://192.168.0.22:81/stream'


def main() -> None:
    print('═' * 60)
    print('  TEST 2b — Stream MJPEG solamente (sin detector ni serial)')
    print('═' * 60)
    print(f'\nConectando al stream: {URL_STREAM}')
    print('Ponte una cara delante de la cámara para reproducir el bug.')
    print('Presiona Q para salir.\n')

    stream = LectorStream(URL_STREAM)

    fps = 0.0
    fps_frames, fps_t0 = 0, time.time()
    n_frames_total = 0

    try:
        while True:
            frame = stream.leer()
            if frame is None:
                time.sleep(0.01)
                continue

            n_frames_total += 1
            ahora = time.time()
            fps_frames += 1
            if ahora - fps_t0 >= 1.0:
                fps = fps_frames / (ahora - fps_t0)
                fps_frames, fps_t0 = 0, ahora
                print(f'  [stats] FPS={fps:5.1f}  frames_totales={n_frames_total}  stream_ok={stream.ok}')

            cv2.putText(frame, f'FPS:{fps:4.1f}',
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.imshow('TEST 2b — Solo Stream', frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    finally:
        stream.cerrar()
        cv2.destroyAllWindows()
        print(f'\nResumen: {n_frames_total} frames recibidos en total, FPS final {fps:.1f}')


if __name__ == '__main__':
    main()
