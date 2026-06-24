# Robot Bob — Descripción del Proyecto y Etapas

## Contexto

Robot asistente conversacional llamado **Bob**, desarrollado por Abdair (estudiante de Ingeniería Informática, UATF, Bolivia) para una feria universitaria. El robot combina seguimiento facial, expresiones OLED animadas y conversación natural mediante IA en la nube.

## Objetivo

Crear un sistema integrado donde el robot:

1. Reconozca rostros de personas que se le acerquen.
2. Reaccione con expresiones faciales OLED y movimiento de cabeza (pan/tilt).
3. Inicie conversación selectivamente — no con todos, solo a veces, simulando comportamiento humano natural.
4. Mantenga conversaciones fluidas mediante STT → LLM → TTS.
5. Tenga comportamiento contextual: movimientos casuales en idle, atención durante presencia, pausas durante pensamiento, etc.

## Hardware

| Componente | Ubicación | Función |
|------------|-----------|---------|
| ESP32 DevKit | COM3 / IP 192.168.0.23 | OLED SH1106 (I2C 21/22), servos pan/tilt (GPIO13/12), mic MAX9814 (GPIO34), speaker PAM8403 (GPIO25) |
| ESP32-CAM | IP 192.168.0.22 | Stream MJPEG en :81/stream |
| Laptop | local | Toda la lógica Python (visión, IA, audio) |

## Software base existente

| Sistema | Carpeta | Tecnología |
|---------|---------|------------|
| Seguimiento facial | `Esp32ThinkerAICam/` | OpenCV + MediaPipe BlazeFace + control servos |
| Chat de voz | `voicechatLap/` | Whisper Groq + Llama 3.3 70B + edge-tts (es-MX-Jorge) |
| Firmware ESP32 | `Esp32/` | MicroPython con OLED + servos + audio TCP |

## Decisiones de diseño confirmadas

- **Hardware:** un solo ESP32 DevKit (COM3 = 192.168.0.23). El firmware ya integra OLED + servos + audio.
- **Audio:** se mantiene laptop mic/speaker (`USE_ROBOT_MIC=False`, `USE_ROBOT_SPEAKER=False`) por estabilidad inicial.
- **Trigger de conversación:** tres modos combinados — cara visible N segundos + probabilidad aleatoria + wake word "Bob" como override.
- **Movimiento IDLE:** aleatorio "humano" (pausas, giros, inclinaciones, como aburrido buscando).
- **Scope:** completo — behavior engine, microsacadas, probabilidad de interacción, personalidad.

---

## Arquitectura final — Hilos del proceso

```
PROCESO PRINCIPAL (main.py)
│
├── [Hilo 1] stream-reader      Lee MJPEG del ESP32-CAM, publica frame_actual
├── [Hilo 2] detector-facial    MediaPipe @14 Hz, publica detecciones
├── [Hilo 3] serial-writer      ÚNICO dueño de COM3, cola priorizada
├── [Hilo 4] behavior-engine    Tick @20 Hz, controla movimiento humano
├── [Hilo 5] voice-pipeline     STT → LLM → TTS, activado por eventos
├── [Hilo 6] wake-monitor       Escucha "Bob" continuamente cuando no hay conversación
└── [Hilo principal] main loop  Display OpenCV + HUD, cap a 20 FPS
```

## Máquina de estados

```
IDLE              → sin cara, movimientos aleatorios aburridos
PRESENCE          → cara detectada, timer de permanencia activo
LISTENING         → grabando audio (gatillado por timer+random o wake word)
THINKING          → STT + LLM procesando
SPEAKING          → reproduciendo TTS, con barge-in habilitado
CONVERSATION_IDLE → entre turnos, timeout de 6s antes de volver a IDLE
```

---

## Etapas del proyecto

### Fase 0 — Preparación

- Crear directorio `robot_bob/` paralelo a los sistemas originales (no modificar nada existente).
- Identificar conflictos críticos: ambos sistemas abren COM3 → SerialException si corren juntos.

**Status:** Completado.

### Fase 1 — SerialManager

**Archivo:** `serial_manager.py`

Único dueño de COM3. Resuelve el conflicto crítico de doble apertura del puerto.

- Cola priorizada: SERVO > ESTADO > SIGUIENDO
- Throttle interno: servo ≥50 ms, OLED ≥80 ms
- Deduplicación de estados consecutivos (< 1s)
- API thread-safe: `cmd_servo(pan, tilt)`, `cmd_estado(estado)`, `cmd_siguiendo(dx, dy)`

**Validación:** `tests/test_1_serial.py` — prueba estados OLED, servos, SIGUIENDO y throttle.

**Status:** Completado y validado (test pasó perfecto).

### Fase 2 — StateMachine

**Archivo:** `state_machine.py`

Máquina de estados central. Eventos `threading.Event` por estado para sincronizar hilos.

- Lógica de trigger combinada (permanencia + probabilidad + wake word)
- Notifica al SerialManager el comando OLED en cada transición
- Cooldown entre conversaciones (evita spam)

**Status:** Completado. Validado indirectamente vía test 2 y test 3.

### Fase 3 — FacialTracker

**Archivo:** `facial_tracker.py`

Encapsula `LectorStream` (socket TCP crudo, evita crash FFmpeg) + `DetectorFacial` (MediaPipe en hilo). Reemplaza llamadas directas a `esp32.write()` por delegación a SerialManager.

- Stream timeout: 5.0 s (subido de 2.0 para tolerar hipos)
- Detector throttleado a 14 Hz
- Parámetros servo recalibrados para 20 Hz del main loop:
  - GANANCIA = 0.025 (era 0.018)
  - ZONA_MUERTA reducida ligeramente
  - ADELANTO_MAX = 12 (era 6)

**Validación:** `tests/test_2_tracker.py` — tracker funciona sin VoicePipeline.

**Optimizaciones críticas descubiertas durante el test:**
- Cap del main loop a 20 FPS (el stream emite a 60-80 FPS y procesarlo todo satura el GIL)
- `cv2.setNumThreads(1)` para evitar contención de CPU
- `SIGUIENDO` solo cada 4 frames

**Status:** Funcional (presentable). Mejoras pendientes opcionales: easing cúbico, predicción Kalman, offset vertical hacia los ojos.

### Fase 4 — VoicePipeline

**Archivo:** `voice_pipeline.py`

Refactor del `chat.py` original. Convierte el loop bloqueante en pipeline activable por eventos.

Componentes:
- Grabación con VAD adaptativo (sounddevice + webrtcvad) desde mic de laptop
- STT con Whisper Large v3 Turbo (Groq API)
- LLM streaming con Llama 3.3 70B (Groq API)
- TTS con edge-tts (voz `es-MX-JorgeNeural`)
- Wake monitor independiente que escucha "Bob" continuamente cuando no hay conversación
- Conversación multi-turno: tras la primera respuesta de Bob, el usuario puede seguir hablando sin repetir "Bob"

**Validación:** `tests/test_3_voice.py` — voz funciona sin tracker.

**Bug fix iterativo durante test:**
- v1: cada turno volvía a IDLE inmediatamente → refactor a `_run_conversation` con loop interno
- v2: requería wake word dos veces (wake monitor + dentro de _run_conversation) → eliminado el doble check

**Status:** Funcional. Caveat: si dices "Bob ¿cómo estás?" todo junto, el wake monitor captura el audio entero pero solo extrae el wake; el resto se pierde. Workaround: decir "Bob" → pausa → pregunta.

### Fase 5 — BehaviorEngine

**Archivo:** `behavior.py`

Motor de comportamiento humano. Tick a 20 Hz.

- **IDLE / SEARCHING:** waypoints aleatorios con pausas variables (0.5-3 s), microsacadas leves
- **PRESENCE:** tracking real + desvíos de mirada ocasionales (cada 3-8 s, 5-15°)
- **THINKING:** pausa + leve inclinación hacia arriba-derecha
- **SPEAKING:** tracking más lento, más desvíos (no fija mirada rígidamente)
- Offset vertical EYE_CONTACT_OFFSET_TILT = -8° para mirar a los ojos en vez del cuello

**Status:** Implementado, pendiente de pruebas integradas.

### Fase 6 — Main orquestador

**Archivo:** `main.py`

Punto de entrada. Arranca componentes en orden:

1. SerialManager (abre COM3)
2. StateMachine
3. FacialTracker (espera primer frame, max 15 s)
4. VoicePipeline + wake monitor
5. BehaviorEngine
6. Loop OpenCV con HUD, cap a 20 FPS, `cv2.setNumThreads(1)`

**Cierre limpio:** todos los hilos daemon, posición home + estado neutro al salir.

**Status:** Implementado, pendiente de validación integral.

### Fase 7 — Tests aislados por componente

**Archivos:** `tests/test_1_serial.py`, `tests/test_2_tracker.py`, `tests/test_2b_stream_only.py`, `tests/test_3_voice.py`

Pruebas que validan cada componente sin dependencias cruzadas.

| Test | Valida | Estado |
|------|--------|--------|
| 1 — serial | COM3, OLED, servos, SIGUIENDO, throttle | ✅ Pasa |
| 2 — tracker | Stream MJPEG + MediaPipe + servos sin voz | ✅ Funcional |
| 2b — stream solo | Diagnóstico del ESP32-CAM aislado | ✅ Confirmó cámara sana |
| 3 — voz | Mic + Whisper + Llama + TTS + OLED estados | ✅ Funcional |

**Status:** Tres tests verdes. Próximo paso: integración final con BehaviorEngine.

---

## Estado actual (2026-06-09)

- **Tests individuales:** 3 de 3 funcionales y presentables.
- **Integración final (main.py + BehaviorEngine activo):** pendiente de validar.
- **Mejoras opcionales identificadas:**
  - Easing cúbico en el suavizado del servo
  - Offset vertical hacia los ojos (parcialmente implementado en BehaviorEngine)
  - Predicción Kalman para reducir lag de seguimiento
  - "Doble toma" al detectar cara nueva tras >30s sin presencia
  - Centro de nariz (landmarks) en vez de bbox center

## Limitaciones conocidas

- **FPS del stream limitado** por ESP32-CAM (~25-30 FPS prácticos)
- **Reconexiones esporádicas** del MJPEG bajo WiFi con ruido (inevitable)
- **Wake word + pregunta en una sola frase** rápida pierde la pregunta (limitación del flujo separado wake_monitor/listening)
- **Audio TCP del ESP32 sin activar** (usamos mic/speaker de laptop por estabilidad inicial)

## Próximos pasos sugeridos

1. **Probar main.py completo** con BehaviorEngine activado.
2. Si el behavior engine reintroduce el problema del stream (saturación CPU), bajar TICK_S del behavior o reducir trabajo por tick.
3. Probar comportamiento de feria realista (varias personas pasando).
4. Decidir si activar audio del ESP32 (mic/speaker) o quedarse con laptop.
5. Pulir personalidad del system prompt para feria.

---

## Estructura del directorio `robot_bob/`

```
robot_bob/
├── AgentsGoal.md           ← este archivo (hoy en docs/HISTORY.md)
├── requirements.txt        ← dependencias Python 3.11
├── serial_manager.py       ← Fase 1
├── state_machine.py        ← Fase 2
├── facial_tracker.py       ← Fase 3
├── voice_pipeline.py       ← Fase 4
├── behavior.py             ← Fase 5
├── main.py                 ← Fase 6
└── tests/
    ├── test_1_serial.py
    ├── test_2_tracker.py
    ├── test_2b_stream_only.py
    └── test_3_voice.py
```

## Dependencias externas

- `opencv-python`, `mediapipe`, `numpy` — visión
- `pyserial` — COM3
- `webrtcvad-wheels`, `sounddevice`, `scipy` — audio + VAD
- `edge-tts`, `imageio-ffmpeg` — TTS
- `groq` — STT + LLM
- `pygame` — reproducción local
- `rich` — UI terminal

Modelo BlazeFace en `Esp32ThinkerAICam/blaze_face_short_range.tflite`.
Config y wake_word importados desde `voicechatLap/`.
