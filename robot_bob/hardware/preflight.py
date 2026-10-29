"""
Preflight de arranque de Bob: verifica red, placas, cámara, COM3, micrófono,
voz (Piper), LLM local (Ollama) y Groq. Imprime VERDE/ROJO por cada punto.

Uso (desde robot_bob/):  python -m hardware.preflight
Sale con 0 si todo está en VERDE; distinto de 0 si algún punto está en ROJO.

No abre COM3 (abrirlo resetea el DevKit): solo verifica que esté listado.
"""
import json
import os
import socket
import sys
import time
import urllib.request

import config   # al importar aplica discovery.devices.json a las IPs
import rutas

TIMEOUT = 3.0
SERIE_LISTADO = config.PUERTO_SERIAL

resultados = []


def registrar(nombre: str, ok: bool, detalle: str) -> None:
    resultados.append(ok)
    print(f"[{'VERDE' if ok else 'ROJO '}] {nombre:<12} {detalle}", flush=True)


def ip_local_hacia(ip: str) -> str:
    """IP de esta laptop en la interfaz que llega a `ip` (UDP connect no envía paquetes)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((ip, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def check_red() -> None:
    try:
        with open(rutas.DEVICES_JSON, encoding="utf-8") as f:
            subred = json.load(f)["subnet"]
        local = ip_local_hacia(config.IP_ESPCAM)
        ok = local.startswith(subred + ".")
        registrar("red", ok, f"laptop {local} · subred de placas {subred}.x")
    except Exception as e:
        registrar("red", False, f"error: {e}")


def check_placas() -> None:
    """Escaneo completo con discovery.py (guarda devices.json) y aplica las IPs
    a config con la misma función que usa el arranque del robot."""
    from hardware import discovery
    antes = {"IP_ESPCAM": config.IP_ESPCAM, "CONTROL_IP": config.CONTROL_IP}
    print("[placas] escaneando la red (puede tardar unos segundos)...", flush=True)
    try:
        roles = discovery.discover(verbose=False)
        config.apply_discovery()   # relee devices.json recién guardado
        faltan = [r for r in ("cam", "control", "audio") if r not in roles]
        if faltan:
            registrar("placas", False,
                      f"sin resolver {faltan}: placa apagada o fuera de la red")
            return
        cambios = [f"{k} {antes[k]} -> {getattr(config, k)}"
                   for k in ("IP_ESPCAM", "CONTROL_IP") if antes[k] != getattr(config, k)]
        detalle = f"CAM {config.IP_ESPCAM} · DevKit {config.CONTROL_IP} (discovery)"
        if cambios:
            detalle += " · cambió: " + ", ".join(cambios)
        registrar("placas", True, detalle)
    except Exception as e:
        registrar("placas", False, f"discovery falló: {e}")


def check_camara() -> None:
    ip = config.IP_ESPCAM
    s = socket.socket()
    s.settimeout(TIMEOUT)
    try:
        s.connect((ip, 81))
        s.sendall(b"GET /stream HTTP/1.0\r\n\r\n")
        cabecera = s.recv(64)
        ok = b"200" in cabecera
        registrar("camara", ok, f"http://{ip}:81/stream -> {'HTTP 200' if ok else cabecera[:20]!r}")
    except OSError as e:
        registrar("camara", False, f"{ip}:81 no responde ({e.__class__.__name__}); reiniciar la CAM")
    finally:
        s.close()


def check_com3() -> None:
    import serial.tools.list_ports as lp
    puertos = [p.device for p in lp.comports()]
    ok = SERIE_LISTADO in puertos
    detalle = f"{SERIE_LISTADO} listado (no se abre)" if ok else f"{SERIE_LISTADO} no aparece en {puertos}"
    registrar("COM3", ok, detalle)


def check_microfono() -> None:
    import numpy as np
    import sounddevice as sd
    from voz.pipeline import _mic_device, _LAP_SAMPLE_RATE
    try:
        dispositivo = _mic_device()
        bloques = []
        with sd.RawInputStream(device=dispositivo, samplerate=_LAP_SAMPLE_RATE, channels=1,
                               dtype="int16", blocksize=1600,
                               callback=lambda indata, n, t, st: bloques.append(bytes(indata))):
            time.sleep(1.5)
        audio = np.frombuffer(b"".join(bloques), dtype=np.int16).astype(np.float32)
        if audio.size == 0:
            registrar("microfono", False, f"dispositivo {dispositivo} no entregó audio")
            return
        rms = float(np.sqrt(np.mean(audio * audio))) / 32767 * 100
        pico = float(np.max(np.abs(audio))) / 32767 * 100
        registrar("microfono", True, f"dispositivo {dispositivo} · RMS {rms:.2f}% · pico {pico:.2f}%")
    except Exception as e:
        registrar("microfono", False, f"error: {e}")


def check_piper() -> None:
    try:
        from piper import PiperVoice, SynthesisConfig
        t0 = time.time()
        voz = PiperVoice.load(config.PIPER_VOICE)
        chunks = list(voz.synthesize("Hola, soy Bob.",
                                     syn_config=SynthesisConfig(length_scale=config.PIPER_LENGTH_SCALE)))
        n = sum(len(c.audio_int16_bytes) for c in chunks)
        ok = n > 0
        registrar("voz", ok, f"Piper {os.path.basename(config.PIPER_VOICE)} · {n} bytes en {time.time() - t0:.1f}s")
    except Exception as e:
        registrar("voz", False, f"error: {e}")


def estado_ollama() -> tuple[bool, str]:
    """(ok, detalle) de Ollama local. También lo usa el vigilante (nucleo/salud.py)."""
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=TIMEOUT) as r:
            modelos = [m["name"] for m in json.load(r)["models"]]
        ok = config.OLLAMA_MODEL in modelos
        return ok, f"{config.OLLAMA_MODEL} {'instalado' if ok else 'NO instalado'}"
    except Exception as e:
        return False, f"no responde en localhost:11434 ({e.__class__.__name__})"


def check_ollama() -> None:
    registrar("ollama", *estado_ollama())


def check_groq() -> None:
    if not config.GROQ_API_KEY:
        registrar("groq", False, "GROQ_API_KEY vacía (revisar robot_bob/.env)")
        return
    try:
        from groq import Groq
        modelos = Groq(api_key=config.GROQ_API_KEY, timeout=TIMEOUT).models.list()
        registrar("groq", True, f"API accesible · {len(modelos.data)} modelos")
    except Exception as e:
        registrar("groq", False, f"error: {str(e)[:80]}")


def main() -> int:
    print("Preflight de Bob", flush=True)
    # Placas primero: las IPs que usan red y cámara salen de discovery.
    for check in [check_placas, check_red, check_camara, check_com3,
                  check_microfono, check_piper, check_ollama, check_groq]:
        check()
    verdes = sum(resultados)
    print(f"\nRESUMEN: {verdes}/{len(resultados)} en VERDE", flush=True)
    return 0 if verdes == len(resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
