"""
test_3_voice_speaker.py — Chat aislado por el PARLANTE del ESP32 (DAC 8-bit/8kHz).

Igual que test_3_voice.py PERO rutea el TTS al parlante del ESP32 (no a la laptop),
para probar de verdad la cadena de filtros 8-bit (speechnorm + EQ). Mic = laptop.

Diferencia clave vs test_3_voice.py:
  - Conecta AudioIO (WiFi TCP) al ESP32 y se lo pasa al VoicePipeline.
  - Fuerza USE_ROBOT_SPEAKER=True por monkeypatch (NO toca config.py).

NO arranca: FacialTracker, BehaviorEngine, cámara.

Prerequisitos hardware:
  - ESP32 DevKit en COM3 (OLED de estados).
  - Firmware de audio del ESP32 corriendo y alcanzable en ESP32_IP:PORT_MIC/PORT_SPK
    (connect() abre AMBOS sockets: mic 5005 + spk 5006).
  - Mic de la laptop + internet (Groq).

Ejecutar:
  cd robot_bob
  python tests/test_3_voice_speaker.py

Detener: Ctrl+C
"""

import os
import socket
import sys
import time

_AQUI = os.path.dirname(__file__)
# Orden importa: el ÚLTIMO insert(0) queda en índice 0. robot_bob debe ganar para
# `from config` (tiene SOLILOQUIO_ENABLED, TTS_FFMPEG_FILTERS, etc.); shared solo
# aporta el módulo audio_io. Por eso shared va primero y robot_bob al final.
sys.path.insert(0, os.path.abspath(os.path.join(_AQUI, '..', '..', 'shared', 'voicechatLap')))  # AudioIO
sys.path.insert(0, os.path.abspath(os.path.join(_AQUI, '..')))                                   # robot_bob/ (gana en `config`)

# Forzar parlante del ESP32 ANTES de usar el pipeline (la bandera se lee como
# global del módulo en _reproducir_mp3). Así no hay que editar config.py.
import voice_pipeline
voice_pipeline.USE_ROBOT_SPEAKER = True

from serial_manager import SerialManager
from state_machine  import StateMachine, RobotState
from voice_pipeline import VoicePipeline
from audio_io       import AudioIO
from config         import ESP32_IP, PORT_MIC, PORT_SPK

PUERTO = 'COM3'
BAUD   = 115200


def main() -> None:
    print('═' * 60)
    print('  TEST VOZ + PARLANTE ESP32 (chat aislado, sin tracker)')
    print('═' * 60)

    mgr = SerialManager(PUERTO, BAUD)
    mgr.cmd_estado('ESPERANDO')
    time.sleep(1.0)

    sm = StateMachine(mgr)

    # El firmware EXIGE conectar mic (5005) Y spk (5006), mic PRIMERO, para
    # arrancar la sesión half-duplex (main.py: srv_mic.accept() luego srv_spk.accept()).
    # Por eso usamos audio.connect() que abre AMBOS en ese orden. El firmware streamea
    # el mic en listen_mode → el hilo lector mantiene connected=True. (El mic del ESP32
    # NO se usa para STT acá: USE_ROBOT_MIC=False → STT toma el mic de la laptop.)
    print(f'\nConectando AudioIO al ESP32 {ESP32_IP} (mic:{PORT_MIC} + spk:{PORT_SPK})...')
    audio = AudioIO()
    try:
        audio.connect(timeout_s=5.0)
    except OSError as e:
        print(f'[audio] ✗ No se pudo conectar: {e}')
        print('        ¿Firmware de audio corriendo? ¿IP correcta (devices.json)? '
              f'¿Puertos {PORT_MIC}/{PORT_SPK} abiertos?')
        mgr.cerrar()
        return
    print(f'[audio] ✓ Conectado (connected={audio.connected}). TTS → parlante ESP32 (8-bit). Mic = laptop.')
    print(f'[audio] USE_ROBOT_SPEAKER={voice_pipeline.USE_ROBOT_SPEAKER}')

    print('\nInicializando VoicePipeline (Groq, edge-tts)...')
    voice = VoicePipeline(mgr, sm, audio_io=audio)
    voice.iniciar_wake_monitor()
    voice.iniciar_recordatorio_monitor()   # P9: dispara recordatorios al vencer (banco P9 completo)

    print('\n' + '─' * 60)
    print('  ESPERANDO WAKE WORD')
    print('─' * 60)
    print('  → Di "Bob" + pregunta. Escucha la respuesta por el PARLANTE del ESP32.')
    print('  → Di "adiós" o Ctrl+C para terminar.')
    print('  → Compara con la cadena vieja (AB-voz-VIEJA.wav) si querés.')
    print('─' * 60 + '\n')

    try:
        ultimo = None
        while True:
            actual = sm.estado.value
            if actual != ultimo:
                print(f'  [estado] {ultimo} → {actual}')
                ultimo = actual
            if sm.estado == RobotState.CONVERSATION_IDLE:
                if not sm.tick_conversation_idle():
                    print('  [info] Timeout de conversación, volviendo a IDLE')
            time.sleep(0.2)
    except KeyboardInterrupt:
        print('\n\nInterrumpido por usuario.')
    finally:
        print('Cerrando...')
        voice.cerrar()
        try:
            audio.close()
        except Exception:
            pass
        mgr.cmd_estado('ESPERANDO')
        time.sleep(0.3)
        mgr.cerrar()
        print('Listo.')


if __name__ == '__main__':
    main()
