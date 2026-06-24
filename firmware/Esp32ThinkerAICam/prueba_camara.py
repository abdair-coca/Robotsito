# prueba_camara.py
# Verifica que la cámara funciona correctamente
# Ejecutar desde Thonny con Python local (no MicroPython)

import cv2

camara = cv2.VideoCapture(0)  # 0 = primera cámara detectada

if not camara.isOpened():
    print('ERROR: No se pudo abrir la camara')
    print('Prueba con VideoCapture(1) si tienes varias camaras')
else:
    print('Camara abierta correctamente')
    print('Presiona Q para salir')

while True:
    ok, frame = camara.read()
    if not ok:
        print('ERROR: No se pudo leer el frame')
        break

    # Mostrar resolución en pantalla
    h, w = frame.shape[:2]
    cv2.putText(frame, f'Resolucion: {w}x{h}', (10, 30),
               cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)

    cv2.imshow('Prueba Camara', frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

camara.release()
cv2.destroyAllWindows()
print('Camara cerrada correctamente')
