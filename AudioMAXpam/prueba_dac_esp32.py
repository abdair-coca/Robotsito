# prueba_dac_esp32.py — CORREGIDO
from machine import DAC, Pin
import utime, math

dac = DAC(Pin(25))	

def tono(frecuencia=440, duracion=2, volumen=100):
    MUESTRAS   = 32                          # puntos por ciclo de la senoidal
    intervalo  = 1_000_000 // (frecuencia * MUESTRAS)  # µs entre muestras
    ciclos     = frecuencia * duracion
    base       = 128

    # Precomputar la tabla senoidal (evita math.sin en el loop)
    tabla = [
        max(0, min(255, base + int(volumen * math.sin(2 * math.pi * i / MUESTRAS))))
        for i in range(MUESTRAS)
    ]

    for _ in range(ciclos):
        for valor in tabla:
            t0 = utime.ticks_us()
            dac.write(valor)
            while utime.ticks_diff(utime.ticks_us(), t0) < intervalo:
                pass

    dac.write(128)  # silencio

print('Tono 440 Hz (La)...')
tono(frecuencia=440, duracion=2)
utime.sleep(1)

print('Tono 880 Hz (La agudo)...')
tono(frecuencia=880, duracion=2)

print('Hecho. Deberias notar el segundo tono mas agudo.')