# seguimiento_facial.py
# Creeper Assistant Robot — Guía 2 (mejorado)
# Seguimiento fluido con filtro de suavizado + predicción de velocidad

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
import serial
import time

# ══════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════
PUERTO_SERIAL = 'COM3'
CAMARA_INDEX  = 0
BAUD_RATE     = 115200

# Control proporcional
GANANCIA_PAN  = 0.04
GANANCIA_TILT = 0.04

# Suavizado lerp: 0.0 = sin moverse, 1.0 = sin suavizado
# 0.12 = movimiento fluido pero responsivo
LERP          = 0.12

# Zona muerta central (px) — el creeper no se mueve si el rostro
# está cerca del centro (evita temblor)
ZONA_MUERTA   = 25

# Predicción: cuánto anticipar el movimiento del rostro
# 0 = sin predicción, 0.3 = anticipa un poco la dirección
PREDICCION    = 0.3

# Límites de ángulo
PAN_MIN,  PAN_MAX  = 20,  160
TILT_MIN, TILT_MAX = 50,  130

# Frecuencia de envío serial
INTERVALO_SERIAL = 0.05  # 20 veces por segundo

# ══════════════════════════════════════════════════════
# INICIALIZACIÓN SERIAL
# ══════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════
# INICIALIZACIÓN CÁMARA
# ══════════════════════════════════════════════════════
camara = cv2.VideoCapture(CAMARA_INDEX)
if not camara.isOpened():
    print('ERROR: No se pudo abrir la camara')
    exit()

# Aumentar buffer mínimo para reducir lag de frames
camara.set(cv2.CAP_PROP_BUFFERSIZE, 1)

W_FRAME   = int(camara.get(cv2.CAP_PROP_FRAME_WIDTH))
H_FRAME   = int(camara.get(cv2.CAP_PROP_FRAME_HEIGHT))
CX_CENTRO = W_FRAME // 2
CY_CENTRO = H_FRAME // 2
print(f'Resolucion: {W_FRAME}x{H_FRAME}')

# ══════════════════════════════════════════════════════
# INICIALIZACIÓN DETECTOR
# ══════════════════════════════════════════════════════
base_options = python.BaseOptions(
    model_asset_path='blaze_face_short_range.tflite'
)
options = vision.FaceDetectorOptions(
    base_options=base_options,
    min_detection_confidence=0.6
)
detector = vision.FaceDetector.create_from_options(options)

# ══════════════════════════════════════════════════════
# ESTADO DEL SEGUIMIENTO
# ══════════════════════════════════════════════════════

# Posición actual de los servos (lo que el servo tiene en este momento)
pan_servo  = 90.0
tilt_servo = 90.0

# Objetivo suavizado (hacia donde queremos ir)
pan_target  = 90.0
tilt_target = 90.0

# Velocidad del rostro para predicción
cx_prev, cy_prev = CX_CENTRO, CY_CENTRO
vx, vy = 0.0, 0.0

# Tiempo para calcular dt real entre frames
t_prev = time.time()
ultimo_envio = 0.0

# Frames sin rostro (para comportamiento de pérdida)
frames_sin_rostro = 0
MAX_FRAMES_SIN_ROSTRO = 30  # ~1 segundo a 30fps

def enviar_comando(pan, tilt):
    """Envía posición al ESP32."""
    if esp32 is None:
        return
    pan  = max(PAN_MIN,  min(PAN_MAX,  int(round(pan))))
    tilt = max(TILT_MIN, min(TILT_MAX, int(round(tilt))))
    try:
        esp32.write(f'H:{pan},V:{tilt}\n'.encode())
    except:
        pass

def lerp(a, b, t):
    """Interpolación lineal."""
    return a + (b - a) * t

def clamp(val, mn, mx):
    return max(mn, min(mx, val))

# ══════════════════════════════════════════════════════
# LOOP PRINCIPAL
# ══════════════════════════════════════════════════════
print('Seguimiento iniciado. Presiona Q para salir')

while True:
    ok, frame = camara.read()
    if not ok:
        break

    ahora = time.time()
    dt    = ahora - t_prev          # tiempo real entre frames
    dt    = clamp(dt, 0.01, 0.1)   # evitar dt extremos
    t_prev = ahora

    # ── Detección ─────────────────────────────────────
    rgb      = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    resultado = detector.detect(mp_image)

    rostro_detectado = False

    if resultado.detections:
        rostro_detectado = True
        frames_sin_rostro = 0

        det = resultado.detections[0]
        bb  = det.bounding_box

        cx = bb.origin_x + bb.width  // 2
        cy = bb.origin_y + bb.height // 2

        # ── Velocidad del rostro (para predicción) ────
        # Filtro exponencial para suavizar la velocidad
        alpha_v = 0.4
        vx = lerp(vx, (cx - cx_prev) / dt, alpha_v)
        vy = lerp(vy, (cy - cy_prev) / dt, alpha_v)
        cx_prev, cy_prev = cx, cy

        # ── Posición predicha (anticipa movimiento) ───
        cx_pred = cx + vx * PREDICCION
        cy_pred = cy + vy * PREDICCION

        # ── Error con zona muerta ──────────────────────
        error_x = cx_pred - CX_CENTRO
        error_y = cy_pred - CY_CENTRO

        # Zona muerta dinámica: más grande cuando el rostro
        # está quieto (vx/vy bajos), más pequeña cuando se mueve
        velocidad = (vx**2 + vy**2) ** 0.5
        zona = ZONA_MUERTA * (1.0 - clamp(velocidad / 300.0, 0, 0.7))

        if abs(error_x) > zona:
            pan_target  -= error_x * GANANCIA_PAN
        if abs(error_y) > zona:
            tilt_target += error_y * GANANCIA_TILT

        pan_target  = clamp(pan_target,  PAN_MIN,  PAN_MAX)
        tilt_target = clamp(tilt_target, TILT_MIN, TILT_MAX)

        # ── Dibujar ───────────────────────────────────
        x, y, w, h = bb.origin_x, bb.origin_y, bb.width, bb.height
        cv2.rectangle(frame, (x,y), (x+w,y+h), (0,255,0), 2)
        cv2.circle(frame, (cx, cy), 5, (0,0,255), -1)

        # Punto predicho (donde va a estar)
        cv2.circle(frame, (int(cx_pred), int(cy_pred)),
                   4, (255,100,0), -1)

        cv2.putText(frame, f'Pan:{pan_servo:.0f} Tilt:{tilt_servo:.0f}',
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,0), 2)
        cv2.putText(frame, f'Error X:{error_x:.0f} Y:{error_y:.0f}',
                    (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,200,255), 2)
        cv2.putText(frame, f'Vel:{velocidad:.0f}px/s',
                    (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200,200,0), 2)

    else:
        frames_sin_rostro += 1

        # Si perdió el rostro por mucho tiempo → volver al centro suavemente
        if frames_sin_rostro > MAX_FRAMES_SIN_ROSTRO:
            pan_target  = lerp(pan_target,  90.0, 0.02)
            tilt_target = lerp(tilt_target, 90.0, 0.02)

        cv2.putText(frame, 'Buscando rostro...',
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,0,200), 2)

    # ── Suavizado lerp: servo sigue al target gradualmente ──
    # Lerp adaptativo: más rápido cuando hay mucho error
    distancia = abs(pan_target - pan_servo) + abs(tilt_target - tilt_servo)
    lerp_rate = clamp(LERP + distancia * 0.003, LERP, 0.35)

    pan_servo  = lerp(pan_servo,  pan_target,  lerp_rate)
    tilt_servo = lerp(tilt_servo, tilt_target, lerp_rate)

    # ── Enviar al ESP32 con control de frecuencia ────
    if ahora - ultimo_envio >= INTERVALO_SERIAL:
        enviar_comando(pan_servo, tilt_servo)
        ultimo_envio = ahora

    # ── Cruz en el centro ────────────────────────────
    cv2.line(frame, (CX_CENTRO-25,CY_CENTRO),
             (CX_CENTRO+25,CY_CENTRO), (255,255,0), 2)
    cv2.line(frame, (CX_CENTRO,CY_CENTRO-25),
             (CX_CENTRO,CY_CENTRO+25), (255,255,0), 2)

    cv2.imshow('Creeper — Seguimiento Facial', frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# ── Limpieza ─────────────────────────────────────────
camara.release()
cv2.destroyAllWindows()
if esp32:
    enviar_comando(90, 90)
    esp32.close()
print('Seguimiento terminado')