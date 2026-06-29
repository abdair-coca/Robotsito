from machine import DAC, Pin
import time

dac = DAC(Pin(26))

VOLUMEN = 0.3  # 0.0 = silencio, 1.0 = máximo

print("Reproduciendo...")
with open("TaylorSwift-23min-esp32.wav", "rb") as f:
    while True:
        chunk = f.read(256)
        if not chunk:
            break
        for sample in chunk:
            # Centrar en 128, aplicar volumen, volver a centrar
            ajustado = int(128 + (sample - 128) * VOLUMEN)
            dac.write(ajustado)
            time.sleep_us(90)

dac.write(128)
print("Listo.")
