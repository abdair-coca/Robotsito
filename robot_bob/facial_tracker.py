"""
facial_tracker.py — Seguimiento facial encapsulado para el robot Bob.

Reutiliza LectorStream y DetectorFacial de seguimiento_facial.py, pero
delega todos los envíos al ESP32 a SerialManager (nunca toca el serial directamente).

API pública:
  FacialTracker.frame_actual  → BGR frame más reciente (o None)
  FacialTracker.ultimo_det    → (cx, cy, x, y, w, h) o None
  FacialTracker.stream_ok     → bool
  FacialTracker.cerrar()
"""

import os
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "quiet"
# FFREPORT: si esta variable EXISTE (con cualquier valor), ffmpeg genera un
# ffmpeg-FECHA-HORA.log por cada invocación. Hay que eliminarla, no setearla.
os.environ.pop("FFREPORT", None)

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision
import socket
import threading
import time
from rich.console import Console
from urllib.parse import urlparse
from rich.console import Console

console = Console(force_terminal=True, color_system="truecolor")

# ── Configuración ──────────────────────────────────────────────────────────────
STREAM_TIMEOUT_S  = 5.0    # tolerante a hipos del ESP32-CAM bajo carga
STREAM_READ_BYTES = 32768
DET_W, DET_H      = 320, 240
DET_INTERVAL_S    = 0.07   # ~14 Hz

# Límites y parámetros de servo
PAN_MIN,  PAN_MAX  = 20,  160
TILT_MIN, TILT_MAX = 40,  130   # TILT_MIN bajo = mira más arriba (límite firmware = 40)
PAN_HOME  = 90
TILT_HOME = 90

GANANCIA_PAN  = 0.025   # más reactivo (era 0.018, calibrado para 60 Hz)
GANANCIA_TILT = 0.025
SIGNO_PAN     = -1
SIGNO_TILT    = +1
ZONA_MUERTA_X = 35      # un poco más chico → seguimiento más fino
ZONA_MUERTA_Y = 30
ADELANTO_MAX  = 12      # deja más margen entre objetivo y actual (más fluido)

# Filtro EMA (media móvil exponencial) sobre el ángulo final del servo.
# Suaviza el jitter residual → movimiento más fluido. alpha en (0, 1]:
#   1.0  = sin filtro (salida = entrada)
#   ~0.5 = suavizado moderado (default)
#   bajo = muy suave pero con más lag
from config import SERVO_EMA_ALPHA

# Cámara montada al revés en la cabeza → rotar el frame 180° tras decodificar.
# La rotación cancela el flip físico: la imagen queda derecha y el tracking
# mantiene los mismos signos de servo.
from config import ROTAR_CAMARA_180


# ── LectorStream (socket TCP crudo — evita crash FFmpeg) ──────────────────────

class LectorStream:
    def __init__(self, url: str, timeout: float = STREAM_TIMEOUT_S):
        u = urlparse(url)
        self.host    = u.hostname
        self.port    = u.port or 80
        self.path    = u.path or '/'
        if u.query:
            self.path += '?' + u.query
        self.timeout = timeout
        self._lock   = threading.Lock()
        self._frame  = None
        self.ok      = False
        self._detener = False
        self._hilo = threading.Thread(target=self._loop, daemon=True, name='stream-reader')
        self._hilo.start()

    def _conectar(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect((self.host, self.port))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:
            pass
        req = (f'GET {self.path} HTTP/1.1\r\n'
               f'Host: {self.host}\r\n'
               f'User-Agent: robot-bob\r\n'
               f'Accept: */*\r\n'
               f'Connection: keep-alive\r\n\r\n')
        s.sendall(req.encode())
        return s

    def _loop(self):
        while not self._detener:
            try:
                sock = self._conectar()
            except Exception as e:
                self.ok = False
                if not self._detener:
                    console.print(f'[yellow][stream] No conecta ({e}). Reintento en 1s...[/]')
                    time.sleep(1)
                continue

            buf = bytearray()
            try:
                while b'\r\n\r\n' not in buf:
                    chunk = sock.recv(STREAM_READ_BYTES)
                    if not chunk:
                        raise ConnectionError('sin cabecera')
                    buf.extend(chunk)
                del buf[:buf.find(b'\r\n\r\n') + 4]

                console.print('[bold green][stream] Conectado.[/]')
                self.ok = True

                while not self._detener:
                    chunk = sock.recv(STREAM_READ_BYTES)
                    if not chunk:
                        raise ConnectionError('stream cerrado')
                    buf.extend(chunk)

                    img = self._extraer_ultimo_jpeg(buf)
                    if img is not None:
                        with self._lock:
                            self._frame = img

                    if len(buf) > 2_000_000:
                        del buf[:-100_000]
            except Exception as e:
                self.ok = False
                if not self._detener:
                    console.print(f'[yellow][stream] Reconectando ({e})...[/]')
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
                if not self._detener:
                    time.sleep(0.2)

    @staticmethod
    def _extraer_ultimo_jpeg(buf: bytearray):
        fin = buf.rfind(b'\xff\xd9')
        if fin == -1:
            return None
        ini = buf.rfind(b'\xff\xd8', 0, fin)
        if ini == -1:
            return None
        jpg = bytes(buf[ini:fin + 2])
        del buf[:fin + 2]
        try:
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None and ROTAR_CAMARA_180:
                img = cv2.rotate(img, cv2.ROTATE_180)
            return img
        except Exception:
            return None

    def leer(self):
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def cerrar(self):
        self._detener = True
        time.sleep(0.1)


# ── DetectorFacial (MediaPipe en hilo background) ─────────────────────────────

class DetectorFacial:
    # Predictor de velocidad: proyecta la cara LOOKAHEAD_S hacia adelante
    # para compensar el lag detección + serial + servo.
    LOOKAHEAD_S = 0.10
    MAX_PRED_PX = 35
    VEL_SMOOTH  = 0.6   # 0..1, más alto = más smoothing (más lag pero menos jitter)

    def __init__(self, detector_mp, w_orig: int, h_orig: int):
        self._detector  = detector_mp
        self._escala_x  = w_orig / DET_W
        self._escala_y  = h_orig / DET_H
        self._lf        = threading.Lock()
        self._lr        = threading.Lock()
        self._frame_pend = None
        self._ocupado    = False
        self._resultado  = None
        self._detener    = threading.Event()
        self._nuevo      = threading.Event()
        # Predictor state
        self._last_cx = None
        self._last_cy = None
        self._last_t  = None
        self._vx      = 0.0
        self._vy      = 0.0
        self._hilo = threading.Thread(target=self._loop, daemon=True, name='detector-facial')
        self._hilo.start()

    def enviar_frame(self, frame) -> None:
        with self._lf:
            if self._ocupado:
                return
            self._frame_pend = frame
        self._nuevo.set()

    @property
    def ultimo_resultado(self):
        with self._lr:
            return self._resultado

    def cerrar(self):
        self._detener.set()
        self._nuevo.set()
        self._hilo.join(timeout=2.0)

    def _loop(self):
        while not self._detener.is_set():
            self._nuevo.wait(timeout=0.5)
            self._nuevo.clear()
            if self._detener.is_set():
                break

            with self._lf:
                frame = self._frame_pend
                self._frame_pend = None
                if frame is None:
                    continue
                self._ocupado = True

            try:
                resultado = self._detectar(frame)
            except Exception:
                resultado = None
            finally:
                with self._lf:
                    self._ocupado = False

            with self._lr:
                self._resultado = resultado

            self._detener.wait(DET_INTERVAL_S)

    def _detectar(self, frame):
        small    = cv2.resize(frame, (DET_W, DET_H), interpolation=cv2.INTER_LINEAR)
        rgb      = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = self._detector.detect(mp_image)

        if not res.detections:
            return None

        det = max(res.detections, key=lambda d: d.bounding_box.width * d.bounding_box.height)
        bb  = det.bounding_box
        x   = int(bb.origin_x * self._escala_x)
        y   = int(bb.origin_y * self._escala_y)
        w   = int(bb.width    * self._escala_x)
        h   = int(bb.height   * self._escala_y)

        # Mejora: usar keypoint nariz (índice 2 en BlazeFace) en vez de bbox center.
        # El bbox center cae cerca del cuello; la nariz cae entre los ojos → más natural.
        cx = x + w // 2
        cy = y + h // 2
        kps = getattr(det, 'keypoints', None)
        if kps and len(kps) >= 3:
            nose = kps[2]
            cx = int(nose.x * DET_W * self._escala_x)
            cy = int(nose.y * DET_H * self._escala_y)

        # Predictor de velocidad: actualiza estimación y proyecta LOOKAHEAD_S adelante.
        now = time.monotonic()
        if self._last_t is not None:
            dt = now - self._last_t
            if 0.01 < dt < 0.3:
                vx = (cx - self._last_cx) / dt
                vy = (cy - self._last_cy) / dt
                self._vx = self.VEL_SMOOTH * self._vx + (1 - self.VEL_SMOOTH) * vx
                self._vy = self.VEL_SMOOTH * self._vy + (1 - self.VEL_SMOOTH) * vy
            else:
                # Hueco grande → reset (no proyectar hacia un futuro estancado)
                self._vx = self._vy = 0.0
        self._last_cx, self._last_cy, self._last_t = cx, cy, now

        dx_pred = max(-self.MAX_PRED_PX, min(self.MAX_PRED_PX, self._vx * self.LOOKAHEAD_S))
        dy_pred = max(-self.MAX_PRED_PX, min(self.MAX_PRED_PX, self._vy * self.LOOKAHEAD_S))
        cx_proy = cx + int(dx_pred)
        cy_proy = cy + int(dy_pred)

        return (cx_proy, cy_proy, x, y, w, h)


# ── FacialTracker — clase principal ───────────────────────────────────────────

class FacialTracker:
    """
    Parámetros de suavizado del servo se inyectan para que BehaviorEngine
    pueda cambiarlos en tiempo de ejecución según el estado del robot.
    """

    def __init__(self, url_stream: str, modelo_tflite: str, serial_mgr, state_machine):
        self._serial  = serial_mgr
        self._sm      = state_machine

        self._stream  = LectorStream(url_stream)

        # Esperar primer frame (máx 10 s)
        self.w = self.h = None
        self.cx_centro = self.cy_centro = None
        self._camera_ready = threading.Event()
        threading.Thread(target=self._esperar_camara, daemon=True).start()

        # El detector se inicializa después de conocer la resolución
        self._detector_mp = None
        self._detector    = None
        self._modelo      = modelo_tflite

        # Estado de servo (actualizado por BehaviorEngine o por el tracker)
        self.pan_actual   = float(PAN_HOME)
        self.tilt_actual  = float(TILT_HOME)
        self.pan_obj      = float(PAN_HOME)
        self.tilt_obj     = float(TILT_HOME)

        # Estado del filtro EMA del ángulo enviado al servo (más fluido).
        self._pan_ema     = float(PAN_HOME)
        self._tilt_ema    = float(TILT_HOME)

    # ── API pública ────────────────────────────────────────────────────────────

    @property
    def stream_ok(self) -> bool:
        return self._stream.ok

    @property
    def camera_ready(self) -> threading.Event:
        return self._camera_ready

    @property
    def ultimo_det(self):
        if self._detector is None:
            return None
        return self._detector.ultimo_resultado

    def leer_frame(self):
        return self._stream.leer()

    def enviar_frame(self, frame) -> None:
        if self._detector:
            self._detector.enviar_frame(frame)

    def actualizar_servo(self, det, suavizado: float = 0.85,
                         max_paso_pan: float = 1.5, max_paso_tilt: float = 1.5) -> None:
        """
        Calcula y envía posición de servo basada en la detección actual.
        BehaviorEngine llama esto con parámetros ajustados al estado.
        """
        if det is not None:
            cx, cy, *_ = det
            error_x = cx - self.cx_centro
            error_y = cy - self.cy_centro

            d_pan  = SIGNO_PAN  * GANANCIA_PAN  * error_x if abs(error_x) > ZONA_MUERTA_X else 0
            d_tilt = SIGNO_TILT * GANANCIA_TILT * error_y if abs(error_y) > ZONA_MUERTA_Y else 0

            self.pan_obj  = _clamp(self.pan_obj  + d_pan,  PAN_MIN,  PAN_MAX)
            self.tilt_obj = _clamp(self.tilt_obj + d_tilt, TILT_MIN, TILT_MAX)

            self.pan_obj  = _clamp(self.pan_obj,  self.pan_actual  - ADELANTO_MAX, self.pan_actual  + ADELANTO_MAX)
            self.tilt_obj = _clamp(self.tilt_obj, self.tilt_actual - ADELANTO_MAX, self.tilt_actual + ADELANTO_MAX)

        # Easing cúbico (smoothstep) sobre el factor de avance:
        # cuando estamos lejos del objetivo, el alpha efectivo es alto (rápido);
        # cuando estamos cerca, el alpha efectivo cae a 0 → aterrizaje suave.
        EASE_SCALE = 12.0  # grados a partir de los cuales avanza a velocidad máxima
        alpha_base = 1.0 - suavizado

        d_pan_full = self.pan_obj  - self.pan_actual
        d_tilt_full = self.tilt_obj - self.tilt_actual

        t_pan  = min(1.0, abs(d_pan_full)  / EASE_SCALE)
        t_tilt = min(1.0, abs(d_tilt_full) / EASE_SCALE)
        # smoothstep: 3t² - 2t³ — derivada nula en t=0 y t=1
        e_pan  = t_pan  * t_pan  * (3.0 - 2.0 * t_pan)
        e_tilt = t_tilt * t_tilt * (3.0 - 2.0 * t_tilt)

        pan_s  = self.pan_actual  + alpha_base * e_pan  * d_pan_full
        tilt_s = self.tilt_actual + alpha_base * e_tilt * d_tilt_full

        dpan   = _clamp(pan_s  - self.pan_actual,  -max_paso_pan,  max_paso_pan)
        dtilt  = _clamp(tilt_s - self.tilt_actual, -max_paso_tilt, max_paso_tilt)

        self.pan_actual  = _clamp(self.pan_actual  + dpan,  PAN_MIN, PAN_MAX)
        self.tilt_actual = _clamp(self.tilt_actual + dtilt, TILT_MIN, TILT_MAX)

        # Filtro EMA final: y[n] = a*x[n] + (1-a)*y[n-1]. Suaviza el jitter
        # residual del ángulo antes de mandarlo al servo → movimiento más fluido.
        a = SERVO_EMA_ALPHA
        self._pan_ema  = a * self.pan_actual  + (1.0 - a) * self._pan_ema
        self._tilt_ema = a * self.tilt_actual + (1.0 - a) * self._tilt_ema

        self._serial.cmd_servo(self._pan_ema, self._tilt_ema)

    def set_objetivo(self, pan: float, tilt: float) -> None:
        """BehaviorEngine inyecta objetivo para movimiento idle."""
        self.pan_obj  = _clamp(pan,  PAN_MIN, PAN_MAX)
        self.tilt_obj = _clamp(tilt, TILT_MIN, TILT_MAX)

    def cerrar(self) -> None:
        if self._detector:
            self._detector.cerrar()
        self._stream.cerrar()

    # ── Interno ────────────────────────────────────────────────────────────────

    def _esperar_camara(self):
        t0 = time.time()
        while time.time() - t0 < 15.0:
            frame = self._stream.leer()
            if frame is not None:
                self.h, self.w = frame.shape[:2]
                self.cx_centro = self.w // 2
                self.cy_centro = self.h // 2
                console.print(f'[bold green][tracker] Camara lista: {self.w}x{self.h}[/]')
                self._iniciar_detector()
                self._camera_ready.set()
                return
            time.sleep(0.05)
        console.print('[red][tracker] ERROR: No llegan frames de la camara.[/]')

    def _iniciar_detector(self):
        base_options = mp_python.BaseOptions(model_asset_path=self._modelo)
        options      = vision.FaceDetectorOptions(
            base_options=base_options,
            min_detection_confidence=0.6,
        )
        self._detector_mp = vision.FaceDetector.create_from_options(options)
        self._detector    = DetectorFacial(self._detector_mp, self.w, self.h)


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v
