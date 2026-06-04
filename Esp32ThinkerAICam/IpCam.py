# encontrar_espcam.py — ejecutar en Thonny
import socket

base   = '192.168.1.'  # <-- ajusta al rango de tu red
puerto = 80            # CameraWebServer usa puerto 80 (no 81)

print(f'Buscando ESP32-CAM en {base}0-254...')
for i in range(1, 255):
    ip = base + str(i)
    try:
        s = socket.socket()
        s.settimeout(0.1)
        s.connect((ip, puerto))
        s.close()
        print(f'Encontrada en: http://{ip}')
        break
    except:
        pass