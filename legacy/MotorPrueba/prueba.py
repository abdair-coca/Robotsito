import serial
esp32 = serial.Serial('COM3', 115200, timeout=1)
import time
time.sleep(2)
esp32.write(b'H:45,V:90\n')  # debe mover el servo pan a la izquierda
print('enviado')
esp32.close()