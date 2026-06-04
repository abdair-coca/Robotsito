# wifi_connect.py  — guardar en el ESP32 DevKit
import network
import time

SSID     = 'Familia Coca Carlo -5'      # Cambia por tu red
PASSWORD = 'FamiliaCoca321'    # Cambia por tu contraseña

def conectar():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        print('Ya conectado:', wlan.ifconfig()[0])
        return wlan.ifconfig()[0]
    print('Conectando al WiFi...')
    wlan.connect(SSID, PASSWORD)
    for _ in range(20):           # espera hasta 10 segundos
        if wlan.isconnected():
            ip = wlan.ifconfig()[0]
            print('Conectado! IP del ESP32:', ip)
            return ip
        time.sleep(0.5)
        print('.', end='')
    raise RuntimeError('No se pudo conectar al WiFi')

if __name__ == '__main__':
    ip = conectar()
    print('IP:', ip)   # ANOTA ESTA IP — la necesitas en la laptop
