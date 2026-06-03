# prueba_deteccion.py
# Detecta el rostro y dibuja el punto central

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

print(mp.__file__)
print(mp.__version__)

# Inicializar detector con la nueva API Tasks
base_options = python.BaseOptions(
    model_asset_path='blaze_face_short_range.tflite'
)
options = vision.FaceDetectorOptions(
    base_options=base_options,
    min_detection_confidence=0.6
)
detector = vision.FaceDetector.create_from_options(options)

camara = cv2.VideoCapture(0)
print('Detectando rostros... Presiona Q para salir')

while True:
    ok, frame = camara.read()
    if not ok:
        break

    h_frame, w_frame = frame.shape[:2]
    centro_frame = (w_frame // 2, h_frame // 2)

    # MediaPipe Tasks necesita su propio formato de imagen
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    resultados = detector.detect(mp_image)

    # Dibujar cruz en el centro del frame
    cv2.line(frame, (centro_frame[0]-20, centro_frame[1]),
             (centro_frame[0]+20, centro_frame[1]), (255,255,0), 2)
    cv2.line(frame, (centro_frame[0], centro_frame[1]-20),
             (centro_frame[0], centro_frame[1]+20), (255,255,0), 2)

    if resultados.detections:
        for det in resultados.detections:
            # Bounding box ahora es absoluto (no relativo)
            bb = det.bounding_box
            x  = bb.origin_x
            y  = bb.origin_y
            w  = bb.width
            h  = bb.height

            # Centro del rostro detectado
            cx = x + w // 2
            cy = y + h // 2

            # Dibujar rectángulo y punto central
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0,255,0), 2)
            cv2.circle(frame, (cx, cy), 5, (0,0,255), -1)

            # Mostrar coordenadas
            cv2.putText(frame, f'Centro: ({cx},{cy})', (x, y-10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,255,0), 2)

            # Calcular error respecto al centro del frame
            error_x = cx - centro_frame[0]
            error_y = cy - centro_frame[1]
            cv2.putText(frame, f'Error X:{error_x} Y:{error_y}', (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,200,255), 2)
    else:
        cv2.putText(frame, 'Sin rostro detectado', (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,255), 2)

    cv2.imshow('Deteccion Facial', frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

camara.release()
cv2.destroyAllWindows()