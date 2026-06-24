# seguimiento_facial.py
# Creeper Assistant Robot - Guia 2 (version mejorada)
# La camara va MONTADA encima de los servos pan/tilt:
# el objetivo es que la cara siempre quede centrada en la imagen.

import os
os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "quiet"
os.environ["FFREPORT"] = "disable"

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import serial
import time
import threading
import socket
from urllib.parse import urlparse

# ══════════════════════════════════════════════════════
# CONFIGURACION
# ══════════════════════════════════════════════════════
PUERTO_SERIAL = 'COM3'           # ESP32 DevKit (servos)
BAUD_RATE     = 115200

IP_ESPCAM   = '192.168.0.22'
URL_STREAM  = f'http://{IP_ESPCAM}:81/stream'

GANANCIA_PAN  = 0.018
GANANCIA_TILT = 0.018

SIGNO_PAN  = -1
SIGNO_TILT = +1

ZONA_MUERTA_X = 40
ZONA_MUERTA_Y = 35

SUAVIZADO = 0.85

MAX_PASO_PAN  = 1.5
MAX_PASO_TILT = 1.5

PAN_MIN,  PAN_MAX  = 20,  160
TILT_MIN, TILT_MAX = 50,  130

PAN_HOME  = 90
TILT_HOME = 90

ADELANTO_MAX = 6

INTERVALO_SERIAL  = 0.05   # 20 Hz max al ESP32
INTERVALO_OLED    = 0.08   # ~12 Hz max para comandos al OLED
TIMEOUT_ROSTRO    = 1.5    # s sin deteccion -> "buscando"

# ══════════════════════════════════════════════════════
# LECTOR DE STREAM MJPEG MANUAL (socket crudo, sin FFmpeg)
# ══════════════════════════════════════════════════════
# cv2.VideoCapture usa FFmpeg/libavformat para leer el MJPEG. Cuando el
# ESP32-CAM manda un paquete corrupto, libavformat dispara un assertion
# (abort() a nivel C) que mata el proceso entero — Python no puede atraparlo.
#
# Aquí abrimos un socket TCP directo, mandamos el GET HTTP a mano y leemos
# el stream con TCP_NODELAY + buffer de recepción grande para drenarlo tan
# rápido como lo hacía FFmpeg (si no se drena rápido, el ESP32 se bloquea
# al enviar y la conexión se estanca). Cada JPEG se decodifica con
# cv2.imdecode: un frame corrupto devuelve None y se salta. FFmpeg nunca
# entra en juego, así que el crash por paquete corrupto es imposible.
STREAM_TIMEOUT_S   = 2.0     # sin datos por este tiempo -> reconectar rápido
STREAM_READ_BYTES  = 32768   # leer en bloques grandes (menos overhead/GIL)

class LectorStream:
    def __init__(self, url, timeout=STREAM_TIMEOUT_S):
        u = urlparse(url)
        self.host    = u.hostname
        self.port    = u.port or 80
        self.path    = u.path or '/'
        if u.query:
            self.path += '?' + u.query
        self.timeout = timeout
        self.lock    = threading.Lock()
        self.frame   = None
        self.ok      = False
        self.detener = False
        self.hilo = threading.Thread(target=self._loop, daemon=True, name='stream-reader')
        self.hilo.start()

    def _conectar(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect((self.host, self.port))
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)  # 1 MB
        except OSError:
            pass
        req = (f'GET {self.path} HTTP/1.1\r\n'
               f'Host: {self.host}\r\n'
               f'User-Agent: creeper\r\n'
               f'Accept: */*\r\n'
               f'Connection: keep-alive\r\n\r\n')
        s.sendall(req.encode())
        return s

    def _loop(self):
        while not self.detener:
            try:
                sock = self._conectar()
            except Exception as e:
                self.ok = False
                if not self.detener:
                    print(f'[stream] No conecta ({e}). Reintento en 1s...')
                    time.sleep(1)
                continue

            buf = bytearray()
            try:
                # Saltar la cabecera HTTP inicial (hasta \r\n\r\n)
                while b'\r\n\r\n' not in buf:
                    chunk = sock.recv(STREAM_READ_BYTES)
                    if not chunk:
                        raise ConnectionError('sin cabecera')
                    buf.extend(chunk)
                del buf[:buf.find(b'\r\n\r\n') + 4]

                print('[stream] Conectado.')
                self.ok = True

                while not self.detener:
                    chunk = sock.recv(STREAM_READ_BYTES)
                    if not chunk:
                        raise ConnectionError('stream cerrado')
                    buf.extend(chunk)

                    # Quedarnos solo con el JPEG completo más reciente (anti-lag)
                    img = self._extraer_ultimo_jpeg(buf)
                    if img is not None:
                        with self.lock:
                            self.frame = img

                    # Salvaguarda contra basura acumulada sin markers
                    if len(buf) > 2_000_000:
                        del buf[:-100_000]
            except Exception as e:
                self.ok = False
                if not self.detener:
                    print(f'[stream] Reconectando ({e})...')
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
                if not self.detener:
                    time.sleep(0.2)

    @staticmethod
    def _extraer_ultimo_jpeg(buf: bytearray):
        """Decodifica el último JPEG completo del buffer y descarta lo anterior.
        Devuelve la imagen BGR, o None si no hay frame completo / está corrupto."""
        fin = buf.rfind(b'\xff\xd9')          # último EOI
        if fin == -1:
            return None
        ini = buf.rfind(b'\xff\xd8', 0, fin)  # último SOI antes de ese EOI
        if ini == -1:
            return None
        jpg = bytes(buf[ini:fin + 2])
        del buf[:fin + 2]                     # tirar todo hasta el frame usado
        try:
            return cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            return None

    def leer(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def cerrar(self):
        self.detener = True
        time.sleep(0.1)


# ══════════════════════════════════════════════════════
# DETECTOR FACIAL EN HILO (nunca bloquea el loop principal)
# ══════════════════════════════════════════════════════
DET_W, DET_H     = 320, 240
DET_INTERVAL_S   = 0.07   # ~14 Hz: suficiente para tracking y deja CPU libre
                          # para que el hilo del stream no se atrase.

class DetectorFacial:
    """
    Corre MediaPipe en un hilo background, throttleado a ~14 Hz.
    - enviar_frame(frame): entrega un frame para procesar.
      Si el hilo está ocupado, descarta el frame (no bloquea).
    - ultimo_resultado: (cx, cy, x, y, w, h) en coords originales, o None.
    """

    def __init__(self, detector_mp, w_orig: int, h_orig: int):
        self._detector   = detector_mp
        self._escala_x   = w_orig / DET_W
        self._escala_y   = h_orig / DET_H
        self._lock_frame = threading.Lock()
        self._lock_res   = threading.Lock()
        self._frame_pend = None          # frame esperando ser procesado
        self._ocupado    = False         # True mientras detect() corre
        self._resultado  = None          # último resultado publicado
        self._detener    = threading.Event()
        self._nuevo      = threading.Event()
        self._hilo = threading.Thread(target=self._loop, daemon=True, name='detector-facial')
        self._hilo.start()

    # --- API pública ---

    def enviar_frame(self, frame) -> None:
        """No bloqueante. Si está ocupado, simplemente descarta."""
        with self._lock_frame:
            if self._ocupado:
                return
            self._frame_pend = frame
        self._nuevo.set()

    @property
    def ultimo_resultado(self):
        with self._lock_res:
            return self._resultado

    def cerrar(self):
        self._detener.set()
        self._nuevo.set()
        self._hilo.join(timeout=2.0)

    # --- Hilo interno ---

    def _loop(self):
        while not self._detener.is_set():
            self._nuevo.wait(timeout=0.5)
            self._nuevo.clear()
            if self._detener.is_set():
                break

            with self._lock_frame:
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
                with self._lock_frame:
                    self._ocupado = False

            with self._lock_res:
                self._resultado = resultado

            # Throttle: ceder CPU al hilo del stream entre detecciones
            self._detener.wait(DET_INTERVAL_S)

    def _detectar(self, frame):
        small    = cv2.resize(frame, (DET_W, DET_H), interpolation=cv2.INTER_LINEAR)
        rgb      = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        resultado = self._detector.detect(mp_image)

        if not resultado.detections:
            return None

        det = rostro_mas_grande(resultado.detections)
        bb  = det.bounding_box

        x  = int(bb.origin_x * self._escala_x)
        y  = int(bb.origin_y * self._escala_y)
        w  = int(bb.width    * self._escala_x)
        h  = int(bb.height   * self._escala_y)
        cx = x + w // 2
        cy = y + h // 2
        return (cx, cy, x, y, w, h)


# ══════════════════════════════════════════════════════
# INICIALIZACION
# ══════════════════════════════════════════════════════

# --- ESP32 (sin resetear) ---
try:
    esp32 = serial.Serial()
    esp32.port          = PUERTO_SERIAL
    esp32.baudrate      = BAUD_RATE
    esp32.timeout       = 1
    esp32.write_timeout = 0.1
    esp32.dtr           = False
    esp32.rts           = False
    esp32.open()
    time.sleep(2)
    print(f'ESP32 conectado en {PUERTO_SERIAL}')
except Exception as e:
    print(f'ERROR Serial: {e}')
    print('Continuando SIN ESP32 (modo solo vision)')
    esp32 = None

# --- Camara ---
print(f'Conectando al stream {URL_STREAM} ...')
stream = LectorStream(URL_STREAM)

# Esperar al primer frame para conocer la resolucion (hasta 10 s)
t0 = time.time()
primer_frame = None
while time.time() - t0 < 10.0:
    primer_frame = stream.leer()
    if primer_frame is not None:
        break
    time.sleep(0.05)

if primer_frame is None:
    print('ERROR: No llegan frames del stream. ¿Está encendido el ESP32-CAM?')
    stream.cerrar()
    if esp32: esp32.close()
    raise SystemExit(1)

H_FRAME, W_FRAME = primer_frame.shape[:2]
CX_CENTRO = W_FRAME // 2
CY_CENTRO = H_FRAME // 2
print(f'Resolucion: {W_FRAME}x{H_FRAME}')

# --- Detector facial (MediaPipe Tasks) ---
base_options = python.BaseOptions(
    model_asset_path='blaze_face_short_range.tflite'
)
options = vision.FaceDetectorOptions(
    base_options=base_options,
    min_detection_confidence=0.6
)
detector_mp = vision.FaceDetector.create_from_options(options)
detector_hilo = DetectorFacial(detector_mp, W_FRAME, H_FRAME)

# --- Estado del control ---
pan_actual    = float(PAN_HOME)
tilt_actual   = float(TILT_HOME)
pan_objetivo  = pan_actual
tilt_objetivo = tilt_actual
ultimo_envio        = 0.0
ultimo_rostro       = 0.0
ultimo_estado_env   = ''
ultimo_envio_oled   = 0.0

# FPS
fps        = 0.0
fps_frames = 0
fps_t0     = time.time()

# ══════════════════════════════════════════════════════
# FUNCIONES
# ══════════════════════════════════════════════════════
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def enviar_comando(pan, tilt):
    if esp32 is None:
        return
    pan_i  = int(clamp(pan,  PAN_MIN,  PAN_MAX))
    tilt_i = int(clamp(tilt, TILT_MIN, TILT_MAX))
    try:
        esp32.write(f'H:{pan_i},V:{tilt_i}\n'.encode())
    except Exception:
        pass

def enviar_estado(estado):
    global ultimo_estado_env, ultimo_envio_oled
    if esp32 is None:
        return
    ahora = time.time()
    if estado == ultimo_estado_env and ahora - ultimo_envio_oled < 1.0:
        return
    ultimo_estado_env  = estado
    ultimo_envio_oled  = ahora
    try:
        esp32.write(f'ESTADO:{estado}\n'.encode())
    except Exception:
        pass

def enviar_siguiendo(error_x, error_y):
    global ultimo_estado_env, ultimo_envio_oled
    if esp32 is None:
        return
    ahora = time.time()
    if ahora - ultimo_envio_oled < INTERVALO_OLED:
        return
    ultimo_estado_env = 'SIGUIENDO'
    ultimo_envio_oled = ahora
    dx = max(-1.0, min(1.0, error_x / (W_FRAME / 2)))
    dy = max(-1.0, min(1.0, error_y / (H_FRAME / 2)))
    try:
        esp32.write(f'SIGUIENDO:{dx:.2f},{dy:.2f}\n'.encode())
    except Exception:
        pass

def rostro_mas_grande(detections):
    mejor, mejor_area = None, -1
    for d in detections:
        bb = d.bounding_box
        area = bb.width * bb.height
        if area > mejor_area:
            mejor_area = area
            mejor = d
    return mejor

# ══════════════════════════════════════════════════════
# LOOP PRINCIPAL
# ══════════════════════════════════════════════════════
print('Seguimiento iniciado. Presiona Q para salir.')
enviar_comando(pan_actual, tilt_actual)

try:
    while True:
        frame = stream.leer()
        if frame is None:
            time.sleep(0.01)
            continue

        # Enviar frame al hilo detector (no bloqueante)
        detector_hilo.enviar_frame(frame)

        ahora = time.time()

        # FPS del loop de display
        fps_frames += 1
        if ahora - fps_t0 >= 1.0:
            fps = fps_frames / (ahora - fps_t0)
            fps_frames = 0
            fps_t0 = ahora

        # Leer último resultado disponible (no bloqueante)
        det = detector_hilo.ultimo_resultado

        error_x = error_y = 0

        if det is not None:
            ultimo_rostro = ahora
            cx, cy, x, y, w, h = det

            error_x = cx - CX_CENTRO
            error_y = cy - CY_CENTRO

            enviar_siguiendo(error_x, error_y)

            # Solo corregimos fuera de la zona muerta
            d_pan  = SIGNO_PAN  * GANANCIA_PAN  * error_x if abs(error_x) > ZONA_MUERTA_X else 0
            d_tilt = SIGNO_TILT * GANANCIA_TILT * error_y if abs(error_y) > ZONA_MUERTA_Y else 0

            pan_objetivo  = clamp(pan_objetivo  + d_pan,  PAN_MIN,  PAN_MAX)
            tilt_objetivo = clamp(tilt_objetivo + d_tilt, TILT_MIN, TILT_MAX)

            pan_objetivo  = clamp(pan_objetivo,
                                  pan_actual  - ADELANTO_MAX, pan_actual  + ADELANTO_MAX)
            tilt_objetivo = clamp(tilt_objetivo,
                                  tilt_actual - ADELANTO_MAX, tilt_actual + ADELANTO_MAX)

            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)
            cv2.line(frame, (CX_CENTRO, CY_CENTRO), (cx, cy), (0, 200, 255), 1)
            cv2.putText(frame, f'Error X:{error_x:+d} Y:{error_y:+d}',
                        (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
        else:
            if ahora - ultimo_rostro > TIMEOUT_ROSTRO:
                cv2.putText(frame, 'Buscando rostro...',
                            (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                enviar_estado('CURIOSO')
            else:
                enviar_estado('ESPERANDO')

        # --- Suavizado exponencial del valor enviado al servo ---
        pan_suave  = SUAVIZADO * pan_actual  + (1 - SUAVIZADO) * pan_objetivo
        tilt_suave = SUAVIZADO * tilt_actual + (1 - SUAVIZADO) * tilt_objetivo

        # --- Limite de paso por ciclo (slew rate) ---
        dpan  = clamp(pan_suave  - pan_actual,  -MAX_PASO_PAN,  MAX_PASO_PAN)
        dtilt = clamp(tilt_suave - tilt_actual, -MAX_PASO_TILT, MAX_PASO_TILT)
        pan_actual  = clamp(pan_actual  + dpan,  PAN_MIN,  PAN_MAX)
        tilt_actual = clamp(tilt_actual + dtilt, TILT_MIN, TILT_MAX)

        # --- Envio al ESP32 con cadencia controlada ---
        if ahora - ultimo_envio >= INTERVALO_SERIAL:
            enviar_comando(pan_actual, tilt_actual)
            ultimo_envio = ahora

        # --- HUD ---
        cv2.putText(frame,
                    f'Pan:{pan_actual:5.1f}  Tilt:{tilt_actual:5.1f}  FPS:{fps:4.1f}',
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        cv2.line(frame, (CX_CENTRO - 25, CY_CENTRO),
                        (CX_CENTRO + 25, CY_CENTRO), (255, 255, 0), 2)
        cv2.line(frame, (CX_CENTRO, CY_CENTRO - 25),
                        (CX_CENTRO, CY_CENTRO + 25), (255, 255, 0), 2)
        cv2.rectangle(frame,
                      (CX_CENTRO - ZONA_MUERTA_X, CY_CENTRO - ZONA_MUERTA_Y),
                      (CX_CENTRO + ZONA_MUERTA_X, CY_CENTRO + ZONA_MUERTA_Y),
                      (120, 120, 120), 1)

        cv2.imshow('Creeper - Seguimiento Facial', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

finally:
    detector_hilo.cerrar()
    stream.cerrar()
    cv2.destroyAllWindows()
    if esp32:
        enviar_comando(PAN_HOME, TILT_HOME)
        time.sleep(0.2)
        esp32.close()
    print('Seguimiento terminado')

# CAMBIOS:
# 1. FIX DEFINITIVO DEL CRASH: LectorStream ya NO usa cv2.VideoCapture (FFmpeg).
#    Lee el stream MJPEG con un socket TCP crudo (GET HTTP a mano) y decodifica
#    cada JPEG con cv2.imdecode. Un paquete corrupto -> None -> se salta.
#    El assertion "pkt->stream_index ... libavformat/utils.c:882" es imposible
#    porque libavformat nunca se usa.
# 2. FIX DEL ESTANCAMIENTO (congelones de 5s):
#    a) Socket con TCP_NODELAY + SO_RCVBUF=1MB + lecturas de 32KB: drena el
#       stream tan rápido como FFmpeg, así el ESP32 no se bloquea al enviar.
#    b) DetectorFacial throttleado a ~14 Hz (DET_INTERVAL_S): MediaPipe ya no
#       satura la CPU, dejando tiempo al hilo del stream (era la causa raíz).
#    c) STREAM_TIMEOUT_S=2.0: si el stream se estanca, reconecta en ~2s en vez
#       de 5s, y la reconexión tarda ~0.2s.
# 3. _extraer_ultimo_jpeg: toma el último par SOI(ffd8)...EOI(ffd9), decodifica
#    solo ese (anti-lag, frame más fresco) y descarta lo viejo.
# 4. DetectorFacial: hilo background, descarta frames si está ocupado. El loop
#    principal nunca espera a MediaPipe.
# 5. HUD muestra FPS para diagnóstico.
# 6. Lógica de servos (PID/suavizado/slew/anti-windup), HUD y serial sin cambios.
