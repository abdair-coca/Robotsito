# Arquitectura — Robot Bob

Cómo está construido el cerebro de Bob (el proceso Python de la laptop). Para
hardware y pines ver [HARDWARE.md](HARDWARE.md); para red/WiFi ver
[NETWORK.md](NETWORK.md); para el roadmap ver [ROADMAP.md](ROADMAP.md).

> El sistema canónico vive en `robot_bob/`. Todo lo demás del repo es base
> histórica o firmware (ver [el índice raíz](../agents.md)). Tras cambiar
> arquitectura o config, corre `graphify update .`.

---

## 1. Qué es

**Bob** es un robot interactivo de feria de **Abdair** (Ing. Informática, UATF,
Potosí, Bolivia). Reconoce caras, mueve la cabeza (pan/tilt) para seguirte,
muestra emociones en una pantalla OLED y conversa por voz en español usando IA en
la nube. Objetivo de diseño: que alguien que hable con Bob 30 s sienta que **lo
entiende emocionalmente**, no que procesa lenguaje. Compañero, no asistente.

El cerebro corre en la **laptop** (MediaPipe + Groq). Los ESP32 solo hacen
servos/OLED/audio (DevKit) y stream de video (CAM).

---

## 2. Hilos del proceso `robot_bob/main.py`

```
PROCESO PRINCIPAL (main.py)
├── [Hilo 1] stream-reader     Lee MJPEG del ESP32-CAM (socket TCP crudo, evita crash FFmpeg)
├── [Hilo 2] detector-facial   MediaPipe BlazeFace @14 Hz, publica detecciones
├── [Hilo 3] serial-writer     ÚNICO dueño del canal de control (COM3 o TCP 5007), cola priorizada
├── [Hilo 4] behavior-engine   Tick @20 Hz, movimiento humano (idle/presence/thinking/speaking)
├── [Hilo 5] voice-pipeline    STT → LLM → TTS, activado por eventos
├── [Hilo 6] wake-monitor      Escucha "Bob" continuamente cuando no hay conversación
└── [Hilo principal] main loop Display OpenCV + HUD, cap a 25 FPS, Q para salir
```

**Orden de arranque:** SerialManager → StateMachine → FacialTracker (espera primer
frame, máx 15 s) → VoicePipeline + wake monitor → BehaviorEngine → loop. Cierre
limpio con **Q**: posición home + estado neutro; todos los hilos son daemon.

**Conflicto crítico:** dos procesos no pueden abrir COM3 a la vez →
`SerialException`. `SerialManager` es el único dueño; nunca llamar `serial.write()`
directo.

---

## 3. Módulos en `robot_bob/`

| Archivo | Rol |
|---|---|
| `main.py` | Orquestador / entrada. |
| `config.py` | **TODA** la config modificable (hardware, IPs, audio, conversación, servos, LLM/TTS, prompt). Gitignoreado. |
| `serial_manager.py` | Único dueño del canal de control. Cola priorizada SERVO > ESTADO > SIGUIENDO, throttle, dedup. WiFi/USB/none. |
| `state_machine.py` | Máquina de estados central + mood drift emocional. |
| `facial_tracker.py` | `LectorStream` (socket MJPEG) + `DetectorFacial` (MediaPipe). Delega al SerialManager. |
| `voice_pipeline.py` | STT (Whisper) → LLM (Llama) → TTS (edge-tts). Wake word, barge-in, multi-turno, parser de tags `[EMO:X]`. |
| `behavior.py` | Motor de movimiento humano @20 Hz (incluye soliloquio/muecas y giro de cuerpo). |
| `expression_engine.py` | Reacciones instantáneas a lo que dice el usuario (`react_to_user_text`). |
| `memory.py` / `memoria_admin.py` | Memoria persistente (cara↔nombre + SQLite) y su CLI de administración. |
| `face_id.py` | Embeddings faciales (InsightFace) para reconocer personas. |
| `discovery.py` | Auto-localiza las IPs de los 3 ESP32 (ver [NETWORK.md](NETWORK.md)). |

Deps vivas fuera de `robot_bob/`: `shared/voicechatLap/` (audio_io TCP, wake_word,
config base) y `firmware/Esp32ThinkerAICam/blaze_face_short_range.tflite` (modelo).

---

## 4. Máquina de estados

```
IDLE → PRESENCE → LISTENING → THINKING → SPEAKING → CONVERSATION_IDLE → (IDLE)
```
- **IDLE**: sin cara, movimientos aleatorios "aburridos" + soliloquio/muecas.
- **PRESENCE**: cara detectada, timer de permanencia; manda `SIGUIENDO` al OLED.
- **LISTENING**: grabando (gatillado por permanencia+probabilidad o wake word "Bob").
- **THINKING**: STT + LLM procesando, cabeza casi quieta.
- **SPEAKING**: reproduce TTS, con barge-in (interrupción por voz) habilitado.
- **CONVERSATION_IDLE**: entre turnos; timeout → vuelve a IDLE.

---

## 5. Sistema emocional (3 capas)

1. **Mood drift** — sentimiento base acumulativo *dentro* de la conversación
   (`mood: float` en `StateMachine`, clamp `[-1,1]`, decay 5 %/turno, reset al terminar).
2. **Instant reaction** — flash de ojos a lo que dice el usuario antes de responder
   (`expression_engine.react_to_user_text`).
3. **Expression tag por frase** — el LLM prefija cada frase con `[EMO:X]`; el parser
   en `voice_pipeline` lo extrae antes del TTS y lo manda al OLED.

Tags (subconjunto de las 21 emociones del firmware OLED): `FELIZ`, `MUY_FELIZ`,
`CURIOSO`, `TRAVIESO`, `PENSANDO`, `SORPRENDIDO`, `CONFUNDIDO`, `TRISTE`, `MUY_TRISTE`,
`AMOR`, `ORGULLOSO`, `ENOJADO`, `SOSPECHANDO`, `HABLANDO` (default). El formato
`[EMO:X]` es **obligatorio** y vive en `SYSTEM_PROMPT`. Cada tag mapea 1:1 a un estado
OLED que ya existe en el firmware (`.mpy`); exponer uno nuevo = agregarlo a
`SYSTEM_PROMPT` + `TTS_EMO_PROSODY` (sin tocar firmware si la cara ya existe).

---

## 6. Backends de IA

| Función | Backend / modelo |
|---|---|
| STT | Groq Whisper `whisper-large-v3-turbo` |
| LLM | Groq `llama-3.3-70b-versatile`, temp 0.8, máx 160 tokens |
| TTS | `edge-tts` voz `es-BO-MarceloNeural` → ffmpeg → uint8 8 kHz |
| VAD | `webrtcvad-wheels` con gate de ruido adaptativo |
| Wake word | "bob" / "hola bob" / "hey bob"… vía Whisper + fuzzy match |

Audio actual: **mic y speaker de la laptop** (`USE_ROBOT_MIC=False`,
`USE_ROBOT_SPEAKER=False`) por estabilidad. Si se activan, `main.py` conecta el
`AudioIO` TCP del ESP32 (de `shared/voicechatLap/`); si el TCP falla, cae a laptop.

**Secretos:** `GROQ_API_KEY` se lee del entorno con un cargador `.env` manual.
`robot_bob/.env` (real, gitignoreado) · `robot_bob/.env.example` (plantilla). Clave en
https://console.groq.com/keys.

---

## 7. Cómo correr y testear

Entorno: **Python 3.11**, venv en `robot_bob/venv311/`. Deps en
`robot_bob/requirements.txt`.

```bash
# desde robot_bob/, con venv311 activo
python discovery.py       # refresca IPs de los ESP32 (tras cambiar de WiFi)
python main.py            # sistema completo (Q para salir)

python tests/test_1_serial.py        # COM3 / OLED / servos / throttle
python tests/test_2_tracker.py       # stream MJPEG + MediaPipe + servos (sin voz)
python tests/test_2b_stream_only.py  # diagnóstico ESP32-CAM aislado
python tests/test_3_voice.py         # mic + Whisper + Llama + TTS + OLED
python tests/test_5_integration.py   # integración
python tests/test_6_multipersona.py  # varias personas (escenario feria)
python tests/test_memoria.py         # memoria persistente
```

> Skills de Claude Code para el flujo guiado: `bob-run`, `bob-test`, `bob-diagnose`,
> `bob-add-state`.

---

## 8. Convenciones

- **Idioma:** código, comentarios y docs en **español**.
- **Config centralizada:** todo lo ajustable va en `robot_bob/config.py`, no hardcodear.
- **Knowledge graph:** `graphify query "..."` / `path` / `explain` para arquitectura;
  `graphify update .` tras cambiar código.
- **Secretos:** nunca hardcodear claves; van en `.env`.
- **Cable como fallback de oro:** la arquitectura por USB COM3 nunca se elimina; es el
  patrón contra el que se compara el WiFi.
