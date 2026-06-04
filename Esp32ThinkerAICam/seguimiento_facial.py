# seguimiento_facial.py
# Creeper Assistant Robot - Guia 2 (version mejorada)
# La camara va MONTADA encima de los servos pan/tilt:
# el objetivo es que la cara siempre quede centrada en la imagen.

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import serial
import time
import threading

# ══════════════════════════════════════════════════════
# CONFIGURACION
# ══════════════════════════════════════════════════════
PUERTO_SERIAL = 'COM3'           # ESP32 DevKit (servos)
BAUD_RATE     = 115200

IP_ESPCAM   = '192.168.0.22'
URL_STREAM  = f'http://{IP_ESPCAM}:81/stream'

# --- Control proporcional ---
# Grados de servo por pixel de error. Mas alto = reacciona rapido
# pero oscila; mas bajo = suave pero lento.
# Bajos a proposito: la camara va SOBRE los servos, asi que mover
# el servo cambia la imagen -> ganancias altas hacen que se pase
# del centro y oscile ("se vuelve loco").
GANANCIA_PAN  = 0.018
GANANCIA_TILT = 0.018

# --- Direccion del servo segun su montaje ---
# Si al detectar la cara el robot mira al lado contrario,
# invierte el signo correspondiente (+1 / -1).
SIGNO_PAN  = -1   # pan: imagen->derecha (error_x>0) debe mover camara a la derecha
SIGNO_TILT = +1   # tilt: imagen->abajo (error_y>0) debe inclinar camara hacia abajo

# --- Zona muerta (pixeles) ---
# Dentro de esta franja alrededor del centro NO se mueve el servo.
# Amplia: si la cara ya esta "cerca" del centro, se queda quieto en
# vez de perseguir el pixel exacto (que es imposible y causa temblor).
ZONA_MUERTA_X = 40
ZONA_MUERTA_Y = 35

# --- Suavizado exponencial del objetivo del servo ---
# 0 = sin suavizado, 0.9 = muy suave. Alto = movimiento lento y estable.
SUAVIZADO = 0.85

# --- Limite de movimiento por ciclo (grados) ---
# Tope duro de velocidad del servo. Bajo = se mueve despacio y no se pasa.
MAX_PASO_PAN  = 1.5
MAX_PASO_TILT = 1.5

# --- Limites mecanicos del servo ---
PAN_MIN,  PAN_MAX  = 20,  160
TILT_MIN, TILT_MAX = 50,  130

# --- Posicion inicial / reposo ---
PAN_HOME  = 90
TILT_HOME = 90

# --- Anti-windup ---
# El objetivo no puede adelantarse mas de esto (grados) respecto a la
# posicion real del servo. Evita que la latencia del stream acumule
# error y el servo se pase de largo del centro.
ADELANTO_MAX = 6

# --- Tiempos ---
INTERVALO_SERIAL  = 0.05   # 20 Hz max al ESP32
TIMEOUT_ROSTRO    = 1.5    # s sin deteccion -> "buscando"

# ══════════════════════════════════════════════════════
# LECTOR DE FRAMES EN HILO (anti-lag del stream MJPEG)
# ══════════════════════════════════════════════════════
# El stream del ESP32-CAM acumula frames en buffer. Si leemos
# en el bucle principal nos llegan frames viejos y el seguimiento
# va con retraso. Este hilo descarta siempre el frame anterior.
class LectorStream:
    def __init__(self, url):
        self.cap = cv2.VideoCapture(url)
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        self.lock = threading.Lock()
        self.frame = None
        self.ok = self.cap.isOpened()
        self.detener = False
        if self.ok:
            self.hilo = threading.Thread(target=self._loop, daemon=True)
            self.hilo.start()

    def _loop(self):
        while not self.detener:
            ok, f = self.cap.read()
            if not ok:
                time.sleep(0.01)
                continue
            with self.lock:
                self.frame = f

    def leer(self):
        with self.lock:
            return None if self.frame is None else self.frame.copy()

    def cerrar(self):
        self.detener = True
        time.sleep(0.05)
        self.cap.release()

# ══════════════════════════════════════════════════════
# INICIALIZACION
# ══════════════════════════════════════════════════════

# --- ESP32 (sin resetear) ---
try:
    esp32 = serial.Serial()
    esp32.port     = PUERTO_SERIAL
    esp32.baudrate = BAUD_RATE
    esp32.timeout  = 1
    esp32.dtr      = False
    esp32.rts      = False
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
if not stream.ok:
    print('ERROR: No se pudo abrir la camara')
    if esp32: esp32.close()
    exit()

# Esperar al primer frame para conocer la resolucion
t0 = time.time()
primer_frame = None
while time.time() - t0 < 5.0:
    primer_frame = stream.leer()
    if primer_frame is not None:
        break
    time.sleep(0.05)

if primer_frame is None:
    print('ERROR: No llegan frames del stream')
    stream.cerrar()
    if esp32: esp32.close()
    exit()

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
detector = vision.FaceDetector.create_from_options(options)

# --- Estado del control ---
pan_actual   = float(PAN_HOME)
tilt_actual  = float(TILT_HOME)
pan_objetivo  = pan_actual
tilt_objetivo = tilt_actual
ultimo_envio   = 0.0
ultimo_rostro  = 0.0

# ══════════════════════════════════════════════════════
# FUNCIONES
# ══════════════════════════════════════════════════════
def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v

def enviar_comando(pan, tilt):
    if esp32 is None:
        return

    pan_i = int(clamp(pan, PAN_MIN, PAN_MAX))
    tilt_i = int(clamp(tilt, TILT_MIN, TILT_MAX))

    comando = f'H:{pan_i},V:{tilt_i}'

    try:
        esp32.write((comando + '\n').encode())
    except Exception as e:
        print(e)

def rostro_mas_grande(detections):
    """Elige la deteccion con mayor area (rostro mas cercano/dominante)."""
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
# Llevar servos a la posicion inicial
enviar_comando(pan_actual, tilt_actual)

try:
    while True:
        frame = stream.leer()
        if frame is None:
            time.sleep(0.01)
            continue

        ahora = time.time()
        rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        resultado = detector.detect(mp_image)

        error_x = error_y = 0
        det = rostro_mas_grande(resultado.detections) if resultado.detections else None

        if det is not None:
            ultimo_rostro = ahora
            bb = det.bounding_box
            cx = bb.origin_x + bb.width  // 2
            cy = bb.origin_y + bb.height // 2

            error_x = cx - CX_CENTRO
            error_y = cy - CY_CENTRO

            # Solo corregimos fuera de la zona muerta
            d_pan  = SIGNO_PAN  * GANANCIA_PAN  * error_x if abs(error_x) > ZONA_MUERTA_X else 0
            d_tilt = SIGNO_TILT * GANANCIA_TILT * error_y if abs(error_y) > ZONA_MUERTA_Y else 0

            pan_objetivo  = clamp(pan_objetivo  + d_pan,  PAN_MIN,  PAN_MAX)
            tilt_objetivo = clamp(tilt_objetivo + d_tilt, TILT_MIN, TILT_MAX)

            # Anti-windup: el objetivo no puede irse mas de ADELANTO_MAX
            # grados por delante de donde esta el servo realmente.
            pan_objetivo  = clamp(pan_objetivo,
                                  pan_actual  - ADELANTO_MAX, pan_actual  + ADELANTO_MAX)
            tilt_objetivo = clamp(tilt_objetivo,
                                  tilt_actual - ADELANTO_MAX, tilt_actual + ADELANTO_MAX)

            # Dibujo
            x, y, w, h = bb.origin_x, bb.origin_y, bb.width, bb.height
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)
            cv2.line(frame, (CX_CENTRO, CY_CENTRO), (cx, cy), (0, 200, 255), 1)
            cv2.putText(frame, f'Error X:{error_x:+d} Y:{error_y:+d}',
                        (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 2)
        else:
            # Sin rostro: si lleva mucho tiempo perdido, congelamos el objetivo
            if ahora - ultimo_rostro > TIMEOUT_ROSTRO:
                cv2.putText(frame, 'Buscando rostro...',
                            (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)

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
                    f'Pan:{pan_actual:5.1f}  Tilt:{tilt_actual:5.1f}',
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        # Cruz en el centro de la imagen (objetivo: que la cara caiga aqui)
        cv2.line(frame, (CX_CENTRO - 25, CY_CENTRO),
                        (CX_CENTRO + 25, CY_CENTRO), (255, 255, 0), 2)
        cv2.line(frame, (CX_CENTRO, CY_CENTRO - 25),
                        (CX_CENTRO, CY_CENTRO + 25), (255, 255, 0), 2)
        # Rectangulo de zona muerta
        cv2.rectangle(frame,
                      (CX_CENTRO - ZONA_MUERTA_X, CY_CENTRO - ZONA_MUERTA_Y),
                      (CX_CENTRO + ZONA_MUERTA_X, CY_CENTRO + ZONA_MUERTA_Y),
                      (120, 120, 120), 1)

        cv2.imshow('Creeper - Seguimiento Facial', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
finally:
    # ══════════════════════════════════════════════════
    # LIMPIEZA
    # ══════════════════════════════════════════════════
    stream.cerrar()
    cv2.destroyAllWindows()
    if esp32:
        enviar_comando(PAN_HOME, TILT_HOME)
        time.sleep(0.2)
        esp32.close()
    print('Seguimiento terminado')
