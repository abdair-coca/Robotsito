"""
main.py — Robot Bob: seguimiento facial + conversación integrados.

Orden de arranque:
  1. SerialManager  → abre COM3 (único dueño)
  2. StateMachine   → máquina de estados
  3. FacialTracker  → stream MJPEG + MediaPipe (espera camera_ready)
  4. VoicePipeline  → STT/LLM/TTS en hilo daemon
  5. BehaviorEngine → movimiento natural en hilo daemon
  6. Loop principal → display OpenCV + HUD + tecla Q para salir

Presionar Q cierra todo limpiamente.
"""

import os
import sys
import time
import cv2
import threading

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# Limitar OpenCV a 1 hilo evita que se coma todos los cores y deje al
# hilo stream-reader sin CPU para drenar el socket TCP del ESP32-CAM.
cv2.setNumThreads(1)

# ── Configuración ──────────────────────────────────────────────────────────────
# Toda la configuración modificable vive en config.py (en esta misma carpeta).
# Import apply_discovery primero para actualizar IPs via cache antes de bindear
# el resto de valores localmente.
from config import apply_discovery
apply_discovery()
from config import (
    PUERTO_SERIAL, BAUD_RATE,
    IP_ESPCAM, URL_STREAM, MODELO_TFLITE,
    USE_ROBOT_MIC, USE_ROBOT_SPEAKER,
    T_PERMANENCIA_MIN, P_CONVERSACION, COOLDOWN_CONV,
    CONVERSATION_TIMEOUT, TIMEOUT_ROSTRO,
    TARGET_FPS,
)

# Colores HUD (BGR)
COLOR_OK    = (0, 255, 0)
COLOR_WARN  = (0, 200, 255)
COLOR_ERR   = (0, 0, 255)
COLOR_STATE = {
    'IDLE':              (100, 100, 100),
    'PRESENCE':          (0, 200, 100),
    'LISTENING':         (0, 255, 255),
    'THINKING':          (255, 200, 0),
    'SPEAKING':          (0, 180, 255),
    'CONVERSATION_IDLE': (0, 255, 100),
}

# ── Imports de módulos robot_bob ───────────────────────────────────────────────
from serial_manager import SerialManager
from state_machine  import StateMachine, RobotState
from facial_tracker import FacialTracker, PAN_HOME, TILT_HOME, ZONA_MUERTA_X, ZONA_MUERTA_Y
from voice_pipeline import VoicePipeline
from behavior       import BehaviorEngine
try:
    from config import MEMORIA_ENABLED
except Exception:
    MEMORIA_ENABLED = False


def _enrolar_async(frame, face_id, memoria) -> None:
    """Enrolado manual en hilo aparte: no congela el video mientras se escribe
    el nombre en la consola. Memoria es thread-safe (lock interno)."""
    emb, edad = (face_id.analizar(frame) if face_id.listo else (None, None))
    if emb is None:
        print('[enrolar] no hay cara o InsightFace aún no cargó.')
        return
    nombre = input('[enrolar] nombre de la persona: ').strip()
    if not nombre:
        print('[enrolar] cancelado (nombre vacío).')
        return
    m = memoria.reconocer(emb)
    if m:
        memoria.actualizar(m[0], nombre=nombre)
        print(f'[enrolar] actualizado id={m[0]} → {nombre}')
    else:
        pid = memoria.registrar(nombre, emb, edad)
        print(f'[enrolar] nuevo id={pid} → {nombre}')


def main() -> None:
    print('═' * 60)
    print('  Robot Bob — Sistema Integrado')
    print('═' * 60)

    # ── 1. SerialManager ───────────────────────────────────────────────────────
    serial_mgr = SerialManager(PUERTO_SERIAL, BAUD_RATE)
    serial_mgr.cmd_estado('ESPERANDO')

    # ── 2. StateMachine ────────────────────────────────────────────────────────
    sm = StateMachine(
        serial_mgr,
        t_permanencia_min  = T_PERMANENCIA_MIN,
        p_conversacion     = P_CONVERSACION,
        cooldown_conv      = COOLDOWN_CONV,
        conversation_timeout = CONVERSATION_TIMEOUT,
        timeout_rostro     = TIMEOUT_ROSTRO,
    )

    # ── 3. FacialTracker ───────────────────────────────────────────────────────
    print('Conectando al stream de la cámara...')
    tracker = FacialTracker(URL_STREAM, MODELO_TFLITE, serial_mgr, sm)

    print('Esperando primer frame (máx 15 s)...')
    if not tracker.camera_ready.wait(timeout=15.0):
        print('ERROR: No llegan frames de la cámara. ¿Está encendido el ESP32-CAM?')
        serial_mgr.cerrar()
        return

    cx_c = tracker.cx_centro
    cy_c = tracker.cy_centro

    # ── 4. VoicePipeline (con AudioIO opcional al ESP32) ───────────────────────
    audio_io = None
    if USE_ROBOT_MIC or USE_ROBOT_SPEAKER:
        try:
            # Cargar AudioIO desde shared/voicechatLap
            sys.path.append(os.path.abspath(
                os.path.join(os.path.dirname(__file__), '..', 'shared', 'voicechatLap')))
            from audio_io import AudioIO
            print(f'[audio] Conectando al ESP32 audio TCP...')
            audio_io = AudioIO()
            audio_io.connect(timeout_s=5.0)
            print(f'[audio] ✓ Conectado. MIC={USE_ROBOT_MIC} SPEAKER={USE_ROBOT_SPEAKER}')
        except Exception as e:
            print(f'[audio] ✗ No se pudo conectar al ESP32 audio: {e}')
            print(f'[audio]   Fallback automático a laptop (mic + speaker).')
            audio_io = None

    # ── Memoria persistente (P1): reconocimiento facial + SQLite ───────────────
    face_id = memoria = None
    if MEMORIA_ENABLED:
        try:
            from face_id import FaceID
            from memory import Memoria
            face_id = FaceID()                  # carga InsightFace en hilo de fondo
            memoria = Memoria()
            print(f'[memoria] activa. Personas en la base: {memoria.total_personas()}')
        except Exception as e:
            print(f'[memoria] desactivada (no se pudo iniciar): {e}')
            face_id = memoria = None

    voice = VoicePipeline(serial_mgr, sm, audio_io=audio_io,
                          face_id=face_id, memoria=memoria,
                          get_frame=tracker.leer_frame)
    voice.iniciar_wake_monitor()
    voice.iniciar_soliloquio_monitor()   # Bob habla solo cuando está libre
    voice.iniciar_recordatorio_monitor() # P9: anuncia recordatorios al vencer
    voice.iniciar_baile_monitor()        # apaga el baile cuando la música para

    # ── 5. BehaviorEngine ──────────────────────────────────────────────────────
    behavior = BehaviorEngine(sm, tracker)

    # ── Variables de display ───────────────────────────────────────────────────
    fps = 0.0
    fps_frames = 0
    fps_t0 = time.time()
    ultimo_rostro = 0.0
    enroll_hilo: threading.Thread | None = None

    # ── 6. Loop principal ──────────────────────────────────────────────────────
    # IMPORTANTE: cap de FPS (config.TARGET_FPS). El ESP32-CAM emite a 60-80 FPS
    # y procesar cada frame satura el GIL → el stream-reader no drena el socket.
    TARGET_PERIOD = 1.0 / TARGET_FPS

    print('\nRobot Bob activo. Presiona Q para salir.')
    serial_mgr.cmd_servo(PAN_HOME, TILT_HOME)

    try:
        while True:
            t_inicio_ciclo = time.monotonic()

            frame = tracker.leer_frame()
            if frame is None:
                time.sleep(0.01)
                continue

            # Enviar al detector (no bloqueante)
            tracker.enviar_frame(frame)

            ahora = time.time()
            fps_frames += 1
            if ahora - fps_t0 >= 1.0:
                fps = fps_frames / (ahora - fps_t0)
                fps_frames = 0
                fps_t0 = ahora

            det   = tracker.ultimo_det
            estado = sm.estado

            # Tick CONVERSATION_IDLE: si pasó el timeout sin nuevo turno, vuelve a IDLE
            if estado == RobotState.CONVERSATION_IDLE:
                sm.tick_conversation_idle()

            # P3: derivar los estados internos (energía/motivación/curiosidad/sociabilidad)
            sm.tick_estados_internos()

            # ── Notificar cara a la máquina de estados ─────────────────────────
            if det is not None:
                ultimo_rostro = ahora
                sm.notificar_cara(True)

                # Enviar coordenadas SIGUIENDO al OLED si estamos en PRESENCE
                if estado == RobotState.PRESENCE:
                    cx, cy, *_ = det
                    dx = max(-1.0, min(1.0, (cx - cx_c) / (tracker.w / 2)))
                    dy = max(-1.0, min(1.0, (cy - cy_c) / (tracker.h / 2)))
                    serial_mgr.cmd_siguiendo(dx, dy)
            else:
                sm.notificar_cara(False)

            # ── HUD ─────────────────────────────────────────────────────────────
            estado_str = estado.value
            color = COLOR_STATE.get(estado_str, COLOR_OK)

            # Estado del robot
            cv2.putText(frame, f'Estado: {estado_str}',
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            # Servos + FPS
            cv2.putText(frame,
                        f'Pan:{tracker.pan_actual:5.1f}  Tilt:{tracker.tilt_actual:5.1f}  FPS:{fps:4.1f}',
                        (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.65, COLOR_OK, 2)
            # Stream OK indicator
            stream_color = COLOR_OK if tracker.stream_ok else COLOR_ERR
            cv2.circle(frame, (frame.shape[1] - 20, 20), 8, stream_color, -1)

            # P3: estados internos (E=energía M=motivación C=curiosidad S=sociabilidad)
            ei = sm.estados_internos()
            cv2.putText(frame,
                        f"E:{ei['energia']:.2f} M:{ei['motivacion']:.2f} "
                        f"C:{ei['curiosidad']:.2f} S:{ei['sociabilidad']:.2f}",
                        (10, frame.shape[0] - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                        (200, 220, 255), 1)

            if det is not None:
                cx, cy, x, y, w, h = det
                cv2.rectangle(frame, (x, y), (x + w, y + h), COLOR_OK, 2)
                cv2.circle(frame, (cx, cy), 6, COLOR_ERR, -1)
                cv2.line(frame, (cx_c, cy_c), (cx, cy), COLOR_WARN, 1)
                cv2.putText(frame, f'Err X:{cx-cx_c:+d} Y:{cy-cy_c:+d}',
                            (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_WARN, 2)
            else:
                if ahora - ultimo_rostro > TIMEOUT_ROSTRO:
                    cv2.putText(frame, 'Buscando...', (10, 90),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_ERR, 2)

            # Cruz central y zona muerta
            cv2.line(frame, (cx_c - 25, cy_c), (cx_c + 25, cy_c), (255, 255, 0), 2)
            cv2.line(frame, (cx_c, cy_c - 25), (cx_c, cy_c + 25), (255, 255, 0), 2)
            cv2.rectangle(frame,
                          (cx_c - ZONA_MUERTA_X, cy_c - ZONA_MUERTA_Y),
                          (cx_c + ZONA_MUERTA_X, cy_c + ZONA_MUERTA_Y),
                          (80, 80, 80), 1)

            cv2.imshow('Robot Bob', frame)
            tecla = cv2.waitKey(1) & 0xFF
            if tecla == ord('q'):
                break
            elif tecla == ord('e') and memoria is not None and face_id is not None:
                # Enrolado manual (tecla E) en hilo: el video sigue corriendo
                # mientras se escribe el nombre. Frame LIMPIO del tracker (el
                # local ya tiene el HUD dibujado encima de la cara).
                if enroll_hilo is not None and enroll_hilo.is_alive():
                    print('[enrolar] ya hay un enrolado en curso — escribí el nombre en la consola.')
                else:
                    frame_limpio = tracker.leer_frame()
                    if frame_limpio is not None:
                        enroll_hilo = threading.Thread(
                            target=_enrolar_async,
                            args=(frame_limpio.copy(), face_id, memoria),
                            daemon=True, name='enrolar')
                        enroll_hilo.start()

            # Cap a TARGET_FPS — cede CPU al hilo stream-reader.
            elapsed = time.monotonic() - t_inicio_ciclo
            if elapsed < TARGET_PERIOD:
                time.sleep(TARGET_PERIOD - elapsed)

    finally:
        print('\nCerrando...')
        behavior.cerrar()
        voice.cerrar()
        tracker.cerrar()
        if audio_io is not None:
            try:
                audio_io.close()
            except Exception:
                pass
        if memoria is not None:
            try:
                memoria.cerrar()
            except Exception:
                pass
        cv2.destroyAllWindows()
        serial_mgr.cmd_servo(PAN_HOME, TILT_HOME)
        time.sleep(0.3)
        serial_mgr.cmd_estado('ESPERANDO')
        time.sleep(0.1)
        serial_mgr.cerrar()
        print('Robot Bob detenido.')


if __name__ == '__main__':
    main()
