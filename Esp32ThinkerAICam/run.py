"""
run.py — Wrapper de reinicio automático para seguimiento_facial.py.

Uso:
    python run.py

Si seguimiento_facial.py termina limpiamente (returncode 0), este wrapper
también termina. Si termina por un crash (assertion FFmpeg, cv2.error, etc.),
lo reinicia automáticamente tras 3 segundos.
Ctrl+C detiene todo sin reiniciar.
"""

import subprocess
import sys
import time
import os
from datetime import datetime

SCRIPT  = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'seguimiento_facial.py')
DELAY_S = 3

def ts():
    return datetime.now().strftime('%H:%M:%S')

def main():
    intento = 0
    while True:
        intento += 1
        print(f'[{ts()}] Inicio #{intento}')
        try:
            proc = subprocess.run([sys.executable, SCRIPT])
        except KeyboardInterrupt:
            print(f'\n[{ts()}] Detenido por el usuario.')
            return

        code = proc.returncode
        if code == 0:
            print(f'[{ts()}] Terminó limpiamente (code 0). Saliendo.')
            return

        print(f'[{ts()}] Crash detectado (code {code}). Reiniciando en {DELAY_S}s...')
        try:
            time.sleep(DELAY_S)
        except KeyboardInterrupt:
            print(f'\n[{ts()}] Detenido por el usuario.')
            return

if __name__ == '__main__':
    main()
