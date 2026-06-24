---
name: bob-test
description: Ejecutar los tests aislados del robot Bob (serial, tracker, voz) en el orden correcto, esperando validación del usuario entre cada test. Usar cuando el usuario pida "probar el robot", "correr los tests", "test del tracker/voz/serial", o cualquier validación incremental antes de main.py.
---

# Tests del Robot Bob

Hay 4 tests aislados en `robot_bob/tests/`. **Nunca correr `main.py` antes de validar los 3 individuales** — si algo falla en main.py es muy difícil diagnosticar cuál componente es el culpable.

## Orden estricto de ejecución

| # | Archivo | Prerequisito hardware | Lo que valida |
|---|---------|----------------------|---------------|
| 1 | `test_1_serial.py` | ESP32 DevKit en COM3 | OLED + servos + cola priorizada |
| 2b | `test_2b_stream_only.py` | ESP32-CAM en 192.168.0.22 | Stream MJPEG aislado (diagnóstico) |
| 2 | `test_2_tracker.py` | ESP32-CAM + ESP32 DevKit | Tracker completo con MediaPipe |
| 3 | `test_3_voice.py` | Mic laptop + internet | Wake word + STT + LLM + TTS |

## Cómo correr cada test

Desde `scripts/robot_bob/`:

```bash
python tests/test_1_serial.py
python tests/test_2b_stream_only.py   # solo si test 2 falla
python tests/test_2_tracker.py
python tests/test_3_voice.py
```

## Reglas de oro

1. **Esperar al usuario** entre tests. No correr el siguiente hasta que confirme que el anterior funcionó visualmente.
2. **Q en la ventana de video** o **Ctrl+C en terminal** cierra limpiamente. No matar el proceso con TaskKill.
3. **Test 2 falla con reconexiones** → correr test 2b primero. Si 2b funciona, el problema es CPU/GIL. Si 2b también falla, es del ESP32-CAM.
4. **Test 3 requiere internet** para Groq API. Si falla con timeout, verificar conexión y `GROQ_API_KEY` en `shared/voicechatLap/config.py`.

## Criterios de éxito por test

### Test 1
- `[serial] Conectado en COM3` aparece
- OLED muestra 6 caras distintas durante test de estados
- Servos físicamente se mueven a 7 posiciones
- Pupilas del OLED siguen 4 direcciones cardinales en test SIGUIENDO

### Test 2 (y 2b)
- 0-1 reconexiones del stream en 1 minuto
- FPS estable en ~20
- Servos siguen la cara sin saltos bruscos
- Estado HUD alterna IDLE / PRESENCE según cara

### Test 3
- Decir "Bob" + pausa + pregunta → Bob responde
- Siguiente pregunta SIN decir "Bob" → Bob responde (conversación continua)
- "adiós" o "salir" → cierra ordenadamente
- OLED muestra ESCUCHANDO → PENSANDO → HABLANDO en cada turno

## Si algo falla

- Test 1: verificar que no haya otro programa con COM3 abierto (Arduino IDE, PuTTY)
- Test 2: ver `bob-diagnose` para árbol de decisión
- Test 3: revisar `shared/voicechatLap/config.py` (API key, USE_ROBOT_MIC, USE_ROBOT_SPEAKER)
