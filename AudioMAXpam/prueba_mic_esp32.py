# prueba_mic_esp32.py  — guardar en el ESP32 DevKit
from machine import ADC, Pin
import time

MIC_PIN = 34          # GPIO34 = ADC1_CH6
MUESTRAS = 200        # muestras por lectura

adc = ADC(Pin(MIC_PIN))
adc.atten(ADC.ATTN_11DB)    # Rango 0 – 3.3V
adc.width(ADC.WIDTH_12BIT)  # Resolución 12 bits (0 – 4095)

print('Leyendo el micrófono MAX9814 en GPIO34...')
print('Habla o haz un sonido fuerte.')

for i in range(10):
    lecturas = [adc.read() for _ in range(MUESTRAS)]
    minimo  = min(lecturas)
    maximo  = max(lecturas)
    promedio = sum(lecturas) // len(lecturas)
    print(f'  Lectura {i+1}: min={minimo}  max={maximo}  prom={promedio}')
    time.sleep(0.5)

print('OK si max-min > 200 cuando hablas.')
print('Si max-min < 50 siempre, revisa la conexión OUT del MAX9814.')
