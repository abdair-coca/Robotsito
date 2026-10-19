"""
calibrar_mic.py — Calibra WAKE_MIN_LEVEL con el ruido real de la sala y tu voz,
y lo escribe en config.py.

Uso (desde robot_bob/):
  python calibrar_mic.py               # mide, escribe config.py y verifica
  python calibrar_mic.py --solo-medir  # mide y muestra, sin tocar config.py

Mide con la MISMA función que usa el robot (VoicePipeline._grabar_wake_laptop),
con el gate desactivado solo durante la medición. Así el nivel es el que vería
el gate: ventana de WAKE_WINDOW_S, corte temprano al terminar la frase, y RMS en
% de escala completa.

Decisión: umbral = punto medio entre el ruido más alto y la voz más baja. Si la
voz no supera el ruido, no escribe nada. Al escribir, verifica que el valor se
cargue en un proceso nuevo.

Para que el robot use el valor nuevo, reinícialo (main.py o el test).
"""
import io
import os
import re
import subprocess
import sys
import threading
import time
import types
import wave

import numpy as np

import config
import rutas
import voice_pipeline as vp

RUTA_CONFIG = rutas.CONFIG_PY
VENTANA_S = config.WAKE_WINDOW_S
SILENCIO_VENTANAS = 4      # 4 ventanas de silencio (~10 s) para el ruido
INTENTOS = 5


def _gate_falso():
    """Lo que _grabar_wake_laptop necesita de self: estado de conversación y de parada."""
    return types.SimpleNamespace(_sm=types.SimpleNamespace(en_conversacion=False),
                                 _detener=threading.Event())


def capturar_ventana() -> float:
    """Graba una ventana con la función del robot y devuelve su nivel (%)."""
    wav = vp.VoicePipeline._grabar_wake_laptop(_gate_falso())
    if not wav:
        return 0.0
    with wave.open(io.BytesIO(wav), "rb") as wf:
        arr = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr * arr))) / 32767 * 100


def escribir_umbral(valor: float, ruta: str = RUTA_CONFIG) -> None:
    """Reemplaza solo la línea WAKE_MIN_LEVEL. Conserva los saltos de línea del archivo."""
    with open(ruta, encoding="utf-8", newline="") as f:
        texto = f.read()
    nuevo, n = re.subn(r"(?m)^WAKE_MIN_LEVEL\s*=[^\r\n]*",
                       f"WAKE_MIN_LEVEL       = {valor}   # calibrado con calibrar_mic.py",
                       texto)
    if n != 1:
        raise RuntimeError(f"Se esperaba 1 línea WAKE_MIN_LEVEL en config.py y hay {n}")
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        f.write(nuevo)


def verificar_carga(valor: float) -> bool:
    """Carga config.py en un proceso nuevo y compara el valor."""
    out = subprocess.run([sys.executable, "-c", "import config; print(config.WAKE_MIN_LEVEL)"],
                         cwd=os.path.dirname(RUTA_CONFIG), capture_output=True,
                         text=True, encoding="utf-8")
    lineas = [l for l in out.stdout.splitlines() if l.strip()]
    try:
        return bool(lineas) and abs(float(lineas[-1]) - float(valor)) < 1e-9
    except ValueError:
        return False


def main(argv) -> int:
    solo_medir = "--solo-medir" in argv
    print("Calibración del micrófono de Bob")
    print(f"Dispositivo: {vp._mic_device()} · ventana del gate: {VENTANA_S} s · "
          f"WAKE_MIN_LEVEL actual: {config.WAKE_MIN_LEVEL}\n")

    gate_previo = vp.WAKE_MIN_LEVEL
    vp.WAKE_MIN_LEVEL = 0.0    # medir sin que el gate descarte la ventana
    try:
        input(f"1) Presiona ENTER y NO hables durante ~{SILENCIO_VENTANAS * VENTANA_S:.0f} s "
              "(mide el ruido de la sala)...")
        ruido = [capturar_ventana() for _ in range(SILENCIO_VENTANAS)]
        ruido_max = max(ruido)
        print(f"   ruido: máximo {ruido_max:.2f}% · promedio {np.mean(ruido):.2f}%\n")

        input(f"2) Presiona ENTER y di 'Bob, hola' a ~1 m, {INTENTOS} veces "
              "(cada intento se graba solo)...")
        voces = []
        for i in range(1, INTENTOS + 1):
            print(f"   intento {i}: prepárate...", flush=True)
            time.sleep(1.5)
            print("   ¡di 'Bob, hola'!", flush=True)
            v = capturar_ventana()
            voces.append(v)
            print(f"   → nivel: {v:.2f}%")
    finally:
        vp.WAKE_MIN_LEVEL = gate_previo

    voz_min = min(voces)
    print()
    if voz_min <= ruido_max:
        print("NO SE ESCRIBE: tu voz no supera el ruido máximo de la sala. Acércate, sube el "
              "volumen del micrófono Intel en Windows y repite la calibración.")
        return 1

    umbral = round((ruido_max + voz_min) / 2, 2)
    pasan = sum(v >= umbral for v in voces)
    print(f"Voz: mínimo {voz_min:.2f}% · máximo {max(voces):.2f}%")
    print(f"Umbral: WAKE_MIN_LEVEL = {umbral} · pasan {pasan}/{INTENTOS} intentos")

    if solo_medir:
        print("(--solo-medir: config.py no se tocó)")
        return 0 if pasan == INTENTOS else 1

    escribir_umbral(umbral)
    verificado = verificar_carga(umbral)
    print(f"config.py escrito: WAKE_MIN_LEVEL = {umbral} · verificado al cargar: "
          f"{'sí' if verificado else 'NO'}")
    print("Reinicia el robot (main.py o el test) para que use el valor nuevo.")
    return 0 if (verificado and pasan == INTENTOS) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
