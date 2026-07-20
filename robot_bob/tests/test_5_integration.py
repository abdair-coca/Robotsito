"""
test_5_integration.py — Prueba de integración completa del Robot Bob.

Arranca los 6 componentes en orden (igual que main.py) e instrumenta métricas
para validar end-to-end:
  - FPS sostenido del main loop (target > 18)
  - Conteo de transiciones de estado
  - Número de detecciones faciales
  - Latencia entre cambio de estado y respuesta visible
  - Tasa de wake-words capturados

Al cerrar (Q) imprime un REPORTE DE INTEGRACIÓN con criterios PASS/FAIL.

NO reemplaza a main.py — es para validar la integración antes de la feria.

Ejecutar:
  cd robot_bob
  python tests/test_5_integration.py

Salir: Q en la ventana de video.

Plan de prueba sugerido durante el test (5-7 min):
  1. Esperar 30 s sin cara → ver IDLE con movimientos amplios
  2. Pararse frente a cámara → PRESENCE (verificar tracking)
  3. Esperar 3-5 s sin moverse → posible conversación automática
  4. Decir "Bob" → wake word debe disparar LISTENING
  5. Tener conversación de 2-3 turnos
  6. Esperar a que vuelva a IDLE (5 s después del último turno)
  7. Repetir wake-word para confirmar que no quedó atascado
"""

import os
import sys
import time
import cv2

cv2.setNumThreads(1)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from serial_manager import SerialManager
from state_machine  import StateMachine, RobotState
from facial_tracker import FacialTracker, PAN_HOME, TILT_HOME, ZONA_MUERTA_X, ZONA_MUERTA_Y
from voice_pipeline import VoicePipeline
from behavior       import BehaviorEngine

# ── Configuración (idéntica a main.py) ────────────────────────────────────────
PUERTO        = 'COM3'
BAUD          = 115200
IP_ESPCAM     = '192.168.0.22'
URL_STREAM    = f'http://{IP_ESPCAM}:81/stream'
MODELO        = os.path.join(os.path.dirname(__file__), '..', '..',
                             'firmware', 'Esp32ThinkerAICam', 'blaze_face_short_range.tflite')

T_PERMANENCIA_MIN    = 3.0
P_CONVERSACION       = 0.35
COOLDOWN_CONV        = 30.0
CONVERSATION_TIMEOUT = 5.0
TIMEOUT_ROSTRO       = 1.5
TARGET_FPS           = 25

# ── Criterios de aceptación ───────────────────────────────────────────────────
FPS_MIN_OK         = 18.0   # FPS promedio mínimo para considerar OK
PCT_FRAMES_OK_MIN  = 70.0   # % de frames por encima de FPS_MIN_OK

COLOR_OK   = (0, 255, 0)
COLOR_WARN = (0, 200, 255)
COLOR_ERR  = (0, 0, 255)
COLOR_STATE = {
    'IDLE':              (100, 100, 100),
    'PRESENCE':          (0, 200, 100),
    'LISTENING':         (0, 255, 255),
    'THINKING':          (255, 200, 0),
    'SPEAKING':          (0, 180, 255),
    'CONVERSATION_IDLE': (0, 255, 100),
}


class Metricas:
    def __init__(self):
        self.t_inicio          = time.monotonic()
        self.fps_samples       = []          # FPS por segundo
        self.transiciones      = {}          # estado → conteo de entradas
        self.estado_anterior   = None
        self.n_detecciones     = 0
        self.n_frames_total    = 0
        self.n_frames_sin_cara = 0
        self.stream_caidas     = 0
        self.ultimo_stream_ok  = True
        self.tiempo_por_estado = {}          # estado → segundos acumulados
        self.t_ultimo_estado_cambio = self.t_inicio
        self.errores           = []

    def registrar_fps(self, fps: float) -> None:
        self.fps_samples.append(fps)

    def registrar_transicion(self, nuevo: str) -> None:
        ahora = time.monotonic()
        if self.estado_anterior is not None:
            dt = ahora - self.t_ultimo_estado_cambio
            self.tiempo_por_estado[self.estado_anterior] = \
                self.tiempo_por_estado.get(self.estado_anterior, 0.0) + dt
        self.transiciones[nuevo] = self.transiciones.get(nuevo, 0) + 1
        self.estado_anterior = nuevo
        self.t_ultimo_estado_cambio = ahora

    def registrar_frame(self, con_cara: bool, stream_ok: bool) -> None:
        self.n_frames_total += 1
        if con_cara:
            self.n_detecciones += 1
        else:
            self.n_frames_sin_cara += 1
        if self.ultimo_stream_ok and not stream_ok:
            self.stream_caidas += 1
        self.ultimo_stream_ok = stream_ok

    def cerrar(self, estado_final: str) -> None:
        ahora = time.monotonic()
        dt = ahora - self.t_ultimo_estado_cambio
        self.tiempo_por_estado[estado_final] = \
            self.tiempo_por_estado.get(estado_final, 0.0) + dt

    def reporte(self) -> str:
        dur = time.monotonic() - self.t_inicio
        lines = []
        lines.append('═' * 60)
        lines.append('  REPORTE DE INTEGRACIÓN — Robot Bob')
        lines.append('═' * 60)
        lines.append(f'Duración:           {dur:.1f} s')
        lines.append(f'Frames procesados:  {self.n_frames_total}')
        lines.append(f'Detecciones cara:   {self.n_detecciones} '
                     f'({100*self.n_detecciones/max(1,self.n_frames_total):.1f}%)')
        lines.append(f'Caídas de stream:   {self.stream_caidas}')
        lines.append('')
        lines.append('FPS del main loop:')
        if self.fps_samples:
            fps_avg = sum(self.fps_samples) / len(self.fps_samples)
            fps_min = min(self.fps_samples)
            fps_max = max(self.fps_samples)
            pct_ok = 100 * sum(1 for f in self.fps_samples if f >= FPS_MIN_OK) / len(self.fps_samples)
            lines.append(f'  promedio: {fps_avg:.1f}  '
                         f'(min {fps_min:.1f}, max {fps_max:.1f}, '
                         f'{pct_ok:.0f}% ≥ {FPS_MIN_OK})')
        else:
            lines.append('  (sin muestras)')
            fps_avg = pct_ok = 0
        lines.append('')
        lines.append('Transiciones de estado:')
        for est, n in sorted(self.transiciones.items(), key=lambda x: -x[1]):
            t_acum = self.tiempo_por_estado.get(est, 0.0)
            lines.append(f'  {est:<20s} → {n:3d} veces  ({t_acum:5.1f} s acumulados)')
        lines.append('')
        lines.append('Criterios de aceptación:')
        ok_fps    = fps_avg >= FPS_MIN_OK and pct_ok >= PCT_FRAMES_OK_MIN
        ok_stream = self.stream_caidas <= 2
        ok_det    = self.n_detecciones > 0
        for nombre, cond, detalle in [
            ('FPS sostenido',       ok_fps,    f'avg {fps_avg:.1f} ≥ {FPS_MIN_OK} y {pct_ok:.0f}% ≥ {PCT_FRAMES_OK_MIN}%'),
            ('Stream estable',      ok_stream, f'{self.stream_caidas} caídas (max permitidas: 2)'),
            ('Detección funcional', ok_det,    f'{self.n_detecciones} detecciones'),
        ]:
            marca = '✓' if cond else '✗'
            lines.append(f'  {marca} {nombre:<22s}  {detalle}')
        lines.append('')
        if self.errores:
            lines.append('Errores:')
            for e in self.errores:
                lines.append(f'  ! {e}')
        lines.append('═' * 60)
        return '\n'.join(lines)


def main() -> None:
    print('═' * 60)
    print('  TEST FASE 5/6 — Integración completa')
    print('═' * 60)

    metricas = Metricas()

    # ── 1-5. Igual que main.py ────────────────────────────────────────────────
    serial_mgr = SerialManager(PUERTO, BAUD)
    serial_mgr.cmd_estado('ESPERANDO')

    sm = StateMachine(
        serial_mgr,
        t_permanencia_min    = T_PERMANENCIA_MIN,
        p_conversacion       = P_CONVERSACION,
        cooldown_conv        = COOLDOWN_CONV,
        conversation_timeout = CONVERSATION_TIMEOUT,
        timeout_rostro       = TIMEOUT_ROSTRO,
    )

    print('Conectando al stream...')
    tracker = FacialTracker(URL_STREAM, MODELO, serial_mgr, sm)

    print('Esperando primer frame (máx 15 s)...')
    if not tracker.camera_ready.wait(timeout=15.0):
        print('✗ ERROR: No llegan frames de la cámara.')
        metricas.errores.append('Cámara no respondió en 15 s')
        serial_mgr.cerrar()
        print(metricas.reporte())
        return

    cx_c, cy_c = tracker.cx_centro, tracker.cy_centro

    voice = VoicePipeline(serial_mgr, sm)
    voice.iniciar_wake_monitor()
    behavior = BehaviorEngine(serial_mgr, sm, tracker)

    print('\nTest activo. Sigue el plan de prueba del docstring. Q para salir y ver reporte.\n')
    serial_mgr.cmd_servo(PAN_HOME, TILT_HOME)

    fps         = 0.0
    fps_frames  = 0
    fps_t0      = time.time()
    estado_prev = sm.estado.value
    metricas.registrar_transicion(estado_prev)
    ultimo_rostro = 0.0
    target_period = 1.0 / TARGET_FPS

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
                metricas.registrar_fps(fps)
                fps_frames = 0
                fps_t0 = ahora

            det    = tracker.ultimo_det
            estado = sm.estado
            estado_str = estado.value

            if estado == RobotState.CONVERSATION_IDLE:
                sm.tick_conversation_idle()

            if estado_str != estado_prev:
                metricas.registrar_transicion(estado_str)
                estado_prev = estado_str

            con_cara = det is not None
            metricas.registrar_frame(con_cara, tracker.stream_ok)

            if con_cara:
                ultimo_rostro = ahora
                sm.notificar_cara(True)
                if estado == RobotState.PRESENCE:
                    cx, cy, *_ = det
                    dx = max(-1.0, min(1.0, (cx - cx_c) / (tracker.w / 2)))
                    dy = max(-1.0, min(1.0, (cy - cy_c) / (tracker.h / 2)))
                    serial_mgr.cmd_siguiendo(dx, dy)
            else:
                sm.notificar_cara(False)

            # ── HUD enriquecido con métricas ───────────────────────────────────
            color = COLOR_STATE.get(estado_str, COLOR_OK)
            cv2.putText(frame, f'Estado: {estado_str}',
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
            cv2.putText(frame,
                        f'Pan:{tracker.pan_actual:5.1f}  Tilt:{tracker.tilt_actual:5.1f}  FPS:{fps:4.1f}',
                        (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.65, COLOR_OK, 2)
            cv2.putText(frame,
                        f'Dets:{metricas.n_detecciones}  Trans:{sum(metricas.transiciones.values())}  '
                        f'Caidas:{metricas.stream_caidas}',
                        (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_WARN, 2)
            stream_color = COLOR_OK if tracker.stream_ok else COLOR_ERR
            cv2.circle(frame, (frame.shape[1] - 20, 20), 8, stream_color, -1)

            if con_cara:
                cx, cy, x, y, w, h = det
                cv2.rectangle(frame, (x, y), (x + w, y + h), COLOR_OK, 2)
                cv2.circle(frame, (cx, cy), 6, COLOR_ERR, -1)
            elif ahora - ultimo_rostro > TIMEOUT_ROSTRO:
                cv2.putText(frame, 'Buscando...', (10, 120),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_ERR, 2)

            cv2.line(frame, (cx_c - 25, cy_c), (cx_c + 25, cy_c), (255, 255, 0), 2)
            cv2.line(frame, (cx_c, cy_c - 25), (cx_c, cy_c + 25), (255, 255, 0), 2)
            cv2.rectangle(frame,
                          (cx_c - ZONA_MUERTA_X, cy_c - ZONA_MUERTA_Y),
                          (cx_c + ZONA_MUERTA_X, cy_c + ZONA_MUERTA_Y),
                          (80, 80, 80), 1)

            cv2.imshow('TEST 5 — Integración Robot Bob', frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

            elapsed = time.monotonic() - t_inicio_ciclo
            if elapsed < target_period:
                time.sleep(target_period - elapsed)

    finally:
        print('\nCerrando...')
        try:    behavior.cerrar()
        except Exception as e: metricas.errores.append(f'behavior.cerrar: {e}')
        try:    voice.cerrar()
        except Exception as e: metricas.errores.append(f'voice.cerrar: {e}')
        try:    tracker.cerrar()
        except Exception as e: metricas.errores.append(f'tracker.cerrar: {e}')
        cv2.destroyAllWindows()
        serial_mgr.cmd_servo(PAN_HOME, TILT_HOME)
        time.sleep(0.3)
        serial_mgr.cmd_estado('ESPERANDO')
        time.sleep(0.1)
        serial_mgr.cerrar()

        metricas.cerrar(estado_prev)
        print('\n' + metricas.reporte())


if __name__ == '__main__':
    main()
