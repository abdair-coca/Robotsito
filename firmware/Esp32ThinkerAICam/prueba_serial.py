import serial
import time

PUERTO = 'COM3'
BAUD   = 115200

try:
    esp32 = serial.Serial()
    esp32.port     = PUERTO
    esp32.baudrate = BAUD
    esp32.timeout  = 2
    esp32.dtr      = False   # ← evita el reset al conectar
    esp32.rts      = False
    esp32.open()
    time.sleep(2)
    print(f'Conectado al ESP32 en {PUERTO}')
except Exception as e:
    print(f'ERROR al conectar: {e}')
    exit()

comandos = ['H:90,V:90', 'H:0,V:90', 'H:180,V:90',
            'H:90,V:40', 'H:90,V:140', 'H:90,V:90']

for cmd in comandos:
    print(f'Enviando: {cmd}')
    esp32.write((cmd + '\n').encode())
    time.sleep(1.5)

    if esp32.in_waiting:
        respuesta = esp32.readline().decode().strip()
        print(f'  ESP32 responde: {respuesta}')

esp32.close()
print('Prueba serial completada')