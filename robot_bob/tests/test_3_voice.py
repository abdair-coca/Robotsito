"""
test_3_voice.py — Prueba aislada del VoicePipeline (sin tracker ni cámara).

Qué prueba:
  1. Que el micrófono de la laptop graba
  2. Que Whisper (Groq) transcribe correctamente
  3. Que el wake word "Bob" se detecta
  4. Que Llama 3.3 responde
  5. Que edge-tts sintetiza y pygame reproduce
  6. Que el OLED del ESP32 cambia ESCUCHANDO → PENSANDO → HABLANDO

NO arranca: FacialTracker, BehaviorEngine, cámara.

Ejecutar:
  cd robot_bob
  python tests/test_3_voice.py

Detener: Ctrl+C
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from serial_manager import SerialManager
from state_machine  import StateMachine, RobotState
from voice_pipeline import VoicePipeline

PUERTO = 'COM3'
BAUD   = 115200


def main() -> None:
    print('═' * 60)
    print('  TEST FASE 3 — VoicePipeline (sin tracker)')
    print('═' * 60)

    mgr = SerialManager(PUERTO, BAUD)
    mgr.cmd_estado('ESPERANDO')
    time.sleep(1.0)

    sm = StateMachine(mgr)

    print('\nInicializando VoicePipeline (carga Groq, edge-tts, pygame)...')
    voice = VoicePipeline(mgr, sm)
    voice.iniciar_wake_monitor()

    print('\n' + '─' * 60)
    print('  ESPERANDO WAKE WORD')
    print('─' * 60)
    print('  → Di "Bob" en voz alta para iniciar conversación')
    print('  → Después de "Bob", haz una pregunta')
    print('  → Di "adiós" o Ctrl+C para terminar')
    print('  → Observa el OLED: ESCUCHANDO → PENSANDO → HABLANDO')
    print('─' * 60 + '\n')

    try:
        ultimo_estado = None
        while True:
            estado_actual = sm.estado.value
            if estado_actual != ultimo_estado:
                print(f'  [estado] {ultimo_estado} → {estado_actual}')
                ultimo_estado = estado_actual

            # Si estamos en CONVERSATION_IDLE, tick para evaluar timeout
            if sm.estado == RobotState.CONVERSATION_IDLE:
                sigue = sm.tick_conversation_idle()
                if not sigue:
                    print('  [info] Timeout de conversación, volviendo a IDLE')

            time.sleep(0.2)

    except KeyboardInterrupt:
        print('\n\nInterrumpido por usuario.')

    finally:
        print('Cerrando VoicePipeline...')
        voice.cerrar()
        mgr.cmd_estado('ESPERANDO')
        time.sleep(0.3)
        mgr.cerrar()
        print('Listo.')


if __name__ == '__main__':
    main()
