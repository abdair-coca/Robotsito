"""
calibrar_mic.py — Calibra WAKE_MIN_LEVEL en el lugar de la demo.

Uso (en la feria, con el bullicio real):
    python calibrar_mic.py

1. Mide 5 s de ruido ambiente (no hables).
2. Mide 5 s diciendo "Bob" varias veces a la distancia normal de uso.
3. Recomienda el WAKE_MIN_LEVEL exacto para copiar en config.py.

La escala es la misma del gate del wake monitor: RMS int16 / 32767 * 100.
"""

import time

import numpy as np
import sounddevice as sd

SR = 16000
DUR = 5.0


def medir(etiqueta: str) -> tuple:
    """Graba DUR s y devuelve (promedio, pico) de nivel RMS% por ventana de 250 ms."""
    print(f'\n>>> {etiqueta} — {DUR:.0f} segundos...')
    for s in (3, 2, 1):
        print(f'    {s}...')
        time.sleep(1)
    print('    ¡AHORA!')
    audio = sd.rec(int(SR * DUR), samplerate=SR, channels=1, dtype='int16')
    sd.wait()
    a = audio.astype(np.float32).ravel()
    ventana = SR // 4                      # 250 ms
    niveles = []
    for i in range(0, len(a) - ventana, ventana):
        seg = a[i:i + ventana]
        niveles.append(float(np.sqrt(np.mean(seg * seg))) / 32767.0 * 100.0)
    niveles = np.array(niveles)
    return float(niveles.mean()), float(niveles.max())


def main():
    print('═' * 56)
    print('  Calibrador de WAKE_MIN_LEVEL — Robot Bob')
    print('═' * 56)

    amb_prom, amb_pico = medir('RUIDO AMBIENTE: quedate callado')
    print(f'    ambiente: promedio {amb_prom:.2f} | pico {amb_pico:.2f}')

    voz_prom, voz_pico = medir('VOZ: decí "Bob" 3-4 veces como en la demo')
    print(f'    voz:      promedio {voz_prom:.2f} | pico {voz_pico:.2f}')

    print('\n' + '─' * 56)
    if voz_pico <= amb_pico * 1.3:
        print('⚠ Tu voz apenas supera el ruido. Acercate más al micrófono')
        print('  o hablá más fuerte, y calibrá de nuevo.')
        recomendado = amb_pico * 1.15
    else:
        # Punto medio geométrico entre el pico del ambiente y el pico de voz:
        # deja margen a ambos lados.
        recomendado = float(np.sqrt(amb_pico * voz_pico))

    print(f'\n  Copiá esto en robot_bob/config.py:')
    print(f'\n      WAKE_MIN_LEVEL = {recomendado:.2f}\n')
    print(f'  (ambiente pico {amb_pico:.2f} < {recomendado:.2f} < voz pico {voz_pico:.2f})')
    print('─' * 56)


if __name__ == '__main__':
    main()
