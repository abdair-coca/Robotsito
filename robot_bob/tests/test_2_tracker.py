"""
test_2_tracker.py — Prueba aislada del FacialTracker (sin voz).

Qué prueba:
  1. Que se conecta al stream del ESP32-CAM
  2. Que MediaPipe detecta caras
  3. Que los servos siguen la cara
  4. Que el OLED muestra SIGUIENDO con coords correctas
  5. Que la transición IDLE → PRESENCE funciona

NO arranca: VoicePipeline, BehaviorEngine (movimiento idle).

Ejecutar:
  cd robot_bob
  python tests/test_2_tracker.py

Salir: presiona Q en la ventana de video.
"""

import os
import sys
import time
import cv2

# Limitar OpenCV a 1 hilo — evita que OpenCV se coma todos los cores
# y deje al hilo del stream sin CPU para drenar el socket TCP.
cv2.setNumThreads(1)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from serial_manager import SerialManager
from state_machine  import StateMachine, RobotState
from facial_tracker import FacialTracker, PAN_HOME, TILT_HOME, ZONA_MUERTA_X, ZONA_MUERTA_Y

PUERTO     = 'COM3'
BAUD       = 115200
IP_ESPCAM  = '192.168.0.22'
URL_STREAM = f'http://{IP_ESPCAM}:81/stream'
MODELO     = os.path.join(os.path.dirname(__file__), '..', '..', 'firmware', 'Esp32ThinkerAICam', 'blaze_face_short_range.tflite')


def main() -> None:
    print('═' * 60)
    print('  TEST FASE 2 — FacialTracker (sin voz)')
    print('═' * 60)

    # SerialManager + StateMachine (sin VoicePipeline ni Behavior)
    mgr = SerialManager(PUERTO, BAUD)
    mgr.cmd_estado('ESPERANDO')
    time.sleep(1.0)

    # P_CONVERSACION=0 → en este test el robot nunca debe ir a LISTENING (no hay voz)
    sm = StateMachine(mgr, p_conversacion=0.0)

    print(f'\nConectando al stream: {URL_STREAM}')
    tracker = FacialTracker(URL_STREAM, MODELO, mgr, sm)

    print('Esperando primer frame (máx 15 s)...')
    if not tracker.camera_ready.wait(timeout=15.0):
        print('✗ ERROR: No llegan frames. Verifica que el ESP32-CAM esté encendido.')
        mgr.cerrar()
        return

    print(f'✓ Cámara: {tracker.w}x{tracker.h}')
    cx_c, cy_c = tracker.cx_centro, tracker.cy_centro

    mgr.cmd_servo(PAN_HOME, TILT_HOME)
    fps = 0.0
    fps_frames, fps_t0 = 0, time.time()
    n_detecciones = 0
    sig_skip = 0  # contador para no enviar SIGUIENDO cada frame
    SIG_EVERY_N_FRAMES = 4   # ~3 Hz si hay 12 FPS

    # IMPORTANTE: limitar el main loop a 20 FPS. El ESP32-CAM emite a 60-80 FPS,
    # y procesar todo a esa frecuencia satura el GIL y mata al hilo stream-reader.
    TARGET_FPS    = 20
    TARGET_PERIOD = 1.0 / TARGET_FPS

    # Parámetros del servo recalibrados para 20 Hz (originales eran para 60 Hz):
    #   max_paso=3.0° por update → 60°/seg de velocidad máxima
    #   suavizado=0.78 → 22% del objetivo por update (más reactivo)
    SERVO_SUAV  = 0.78
    SERVO_PASO  = 3.0

    print(f'\nTracker activo (max {TARGET_FPS} FPS). Mueve tu cara. Q para salir.\n')

    try:
        while True:
            t_inicio_ciclo = time.monotonic()

            frame = tracker.leer_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            tracker.enviar_frame(frame)
            ahora = time.time()
            fps_frames += 1
            if ahora - fps_t0 >= 1.0:
                fps = fps_frames / (ahora - fps_t0)
                fps_frames, fps_t0 = 0, ahora

            det = tracker.ultimo_det

            if det is not None:
                n_detecciones += 1
                sm.notificar_cara(True)
                cx, cy, x, y, w, h = det

                # Calcular suavizado (manual, sin Behavior engine)
                tracker.actualizar_servo(det, suavizado=SERVO_SUAV,
                                         max_paso_pan=SERVO_PASO, max_paso_tilt=SERVO_PASO)

                # OLED siguiendo — sólo cada N frames (no saturar serial + el OLED ya tiene throttle)
                sig_skip = (sig_skip + 1) % SIG_EVERY_N_FRAMES
                if sig_skip == 0:
                    dx = max(-1.0, min(1.0, (cx - cx_c) / (tracker.w / 2)))
                    dy = max(-1.0, min(1.0, (cy - cy_c) / (tracker.h / 2)))
                    mgr.cmd_siguiendo(dx, dy)

                cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)
                cv2.line(frame, (cx_c, cy_c), (cx, cy), (0, 200, 255), 1)
            else:
                sm.notificar_cara(False)

            estado_str = sm.estado.value
            cv2.putText(frame, f'Estado: {estado_str}',
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame,
                        f'Pan:{tracker.pan_actual:5.1f}  Tilt:{tracker.tilt_actual:5.1f}  FPS:{fps:4.1f}',
                        (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.putText(frame, f'Detecciones: {n_detecciones}',
                        (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 200, 255), 2)

            # Cruz central + zona muerta
            cv2.line(frame, (cx_c - 25, cy_c), (cx_c + 25, cy_c), (255, 255, 0), 2)
            cv2.line(frame, (cx_c, cy_c - 25), (cx_c, cy_c + 25), (255, 255, 0), 2)
            cv2.rectangle(frame,
                          (cx_c - ZONA_MUERTA_X, cy_c - ZONA_MUERTA_Y),
                          (cx_c + ZONA_MUERTA_X, cy_c + ZONA_MUERTA_Y),
                          (80, 80, 80), 1)

            cv2.imshow('TEST 2 — FacialTracker', frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

            # Cap a TARGET_FPS — devuelve CPU al hilo stream-reader.
            # Si el ciclo tardó menos de TARGET_PERIOD, dormimos la diferencia.
            elapsed = time.monotonic() - t_inicio_ciclo
            if elapsed < TARGET_PERIOD:
                time.sleep(TARGET_PERIOD - elapsed)

    finally:
        print('\nCerrando...')
        tracker.cerrar()
        cv2.destroyAllWindows()
        mgr.cmd_servo(PAN_HOME, TILT_HOME)
        mgr.cmd_estado('ESPERANDO')
        time.sleep(0.3)
        mgr.cerrar()
        print(f'\nResumen: {n_detecciones} detecciones procesadas, FPS final {fps:.1f}')


if __name__ == '__main__':
    main()
