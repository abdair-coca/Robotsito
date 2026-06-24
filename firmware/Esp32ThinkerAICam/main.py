# main.py — ESP32-CAM AI-Thinker
# Servidor de stream MJPEG por WiFi

import camera
import network
import socket
import time

from config import SSID, PASSWORD   # credenciales fuera del repo (config.py gitignored)

# Inicializar cámara
camera.init(0,
    format=camera.JPEG,
    framesize=camera.FRAME_VGA,
    quality=12,
    fb_location=camera.PSRAM
)
print('Camara inicializada')

# Conectar a WiFi
wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.connect(SSID, PASSWORD)
print('Conectando a WiFi...')

timeout = 15
while not wlan.isconnected() and timeout > 0:
    time.sleep(1)
    timeout -= 1
    print('.', end='')

if not wlan.isconnected():
    print('ERROR: No se pudo conectar al WiFi')
    raise RuntimeError('WiFi connection failed')

ip = wlan.ifconfig()[0]
print(f'\nConectado! IP: {ip}')
print(f'Stream en: http://{ip}:81/stream')

# Servidor MJPEG
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(('', 81))
server.listen(1)
print('Servidor listo. Esperando conexiones...')

BOUNDARY = b'--frame'
HEADER   = (b'HTTP/1.1 200 OK\r\n'
            b'Content-Type: multipart/x-mixed-replace;'
            b'boundary=frame\r\n\r\n')

while True:
    conn, addr = server.accept()
    print(f'Cliente conectado: {addr}')
    try:
        conn.send(HEADER)
        while True:
            img = camera.capture()
            if img:
                frame_header = (
                    BOUNDARY + b'\r\n'
                    b'Content-Type: image/jpeg\r\n'
                    b'Content-Length: ' + str(len(img)).encode() + b'\r\n\r\n'
                )
                conn.send(frame_header + img + b'\r\n')
    except Exception as e:
        print(f'Cliente desconectado: {e}')
    finally:
        conn.close()