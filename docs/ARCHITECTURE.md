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
├── [Hilo principal] main loop Display OpenCV + HUD, cap a 25 FPS, Q para salir
└── [solo modo CLI] cli-comandos (atajos por stdin) y cli-estado (barra), ver §9
```

**Orden de arranque:** SerialManager → StateMachine → FacialTracker (espera primer
frame, máx 15 s) → VoicePipeline + wake monitor → BehaviorEngine → loop. Cierre
limpio con **Q**: posición home + estado neutro; todos los hilos son daemon.

**Conflicto crítico:** dos procesos no pueden abrir COM3 a la vez →
`SerialException`. `SerialManager` es el único dueño; nunca llamar `serial.write()`
directo.

---

## 3. Módulos en `robot_bob/`

Carpetas por función (reestructura del 2026-10-19). `main.py`, `config.py` y `rutas.py`
quedan en la raíz, y también los datos de runtime (`memory.db`, `devices.json`,
`models/`, `piper_voices/`). Los módulos no calculan rutas con `__file__`: las toman de
`rutas.py`.

| Archivo | Rol |
|---|---|
| `main.py` | Orquestador / entrada. |
| `supervisor.py` | Lanza `main.py` y lo relanza si se cae (para tras 3 caídas en 5 min) o si sale con el código 3 (pide reiniciarse). Es lo que arranca el CLI. |
| `nucleo/salud.py`, `nucleo/avisos_salud.py` | Vigilante de salud: chequea serial, audio, red, Ollama, memoria e hilos de voz, repara lo que tiene receta y lo cuenta por consola, voz, `logs/salud.jsonl` y la barra del CLI. La ESP32-CAM no se vigila. "Bob, diagnostícate" o la tecla V: se revisa entero y dice qué no funciona. |
| `agente/autoarreglo.py` | Autoedición (S6): un error no atrapado en un hilo → Claude Code (Sonnet) lo arregla en `robot_bob/` con el modo ingeniero activo (copias, zonas prohibidas, compila, tests relacionados antes y después; si pasa, pide reiniciarse con el código 3) o, sin modo ingeniero, deja el arreglo propuesto. Informes en `logs/arreglos/`. |
| `config.py` | **TODA** la config modificable (hardware, IPs, audio, conversación, servos, LLM/TTS, prompt). Gitignoreado. |
| `rutas.py` | Rutas de datos (`memory.db`, `devices.json`, `models/`, `logs/`) y de `shared/voicechatLap`. |
| `nucleo/state_machine.py` | Máquina de estados central + mood drift emocional. |
| `nucleo/behavior.py` | Motor de movimiento humano @20 Hz (incluye soliloquio/muecas y giro de cuerpo). |
| `nucleo/expression_engine.py` | Reacciones instantáneas a lo que dice el usuario (`react_to_user_text`). |
| `nucleo/show.py` | Modo presentación ("preséntate"). |
| `nucleo/puente_cli.py` | Con `BOB_CLI=1`: stdout/stderr → eventos NDJSON y comandos por stdin (§9). |
| `nucleo/teclas_cli.py` | Atajos del CLI → cola que lee el loop de `main.py` (§9). |
| `nucleo/estado_cli.py` | Foto del estado de Bob cada 200 ms para la barra del CLI (§9). |
| `hardware/serial_manager.py` | Único dueño del canal de control. Cola priorizada SERVO > ESTADO > SIGUIENDO, throttle, dedup. WiFi/USB/none. |
| `hardware/discovery.py` | Auto-localiza las IPs de los 3 ESP32 (ver [NETWORK.md](NETWORK.md)). |
| `hardware/preflight.py` | Chequeo de arranque: red, placas, cámara, COM3, micrófono, voz, LLM y Groq. |
| `hardware/calibrar_mic.py` | Calibra `WAKE_MIN_LEVEL` con el ruido real de la sala. |
| `vision/facial_tracker.py` | `LectorStream` (socket MJPEG) + `DetectorFacial` (MediaPipe). Delega al SerialManager. |
| `vision/face_id.py` | Embeddings faciales (InsightFace) para reconocer personas. |
| `voz/pipeline/__init__.py` | `VoicePipeline`: STT (Whisper) → LLM (Llama) → TTS (edge-tts). Wake word, barge-in, multi-turno, parser de tags `[EMO:X]`. Las funciones auxiliares están en `voz/pipeline/`: `emocion`, `reglas_turno`, `frases`, `audio`, `tts`, `contexto`, `texto`, `prefetch`, `consola`. |
| `voz/stt.py` | Transcripción (Whisper vía Groq) y filtros de alucinaciones. |
| `voz/audio_engine.py` | Micrófono continuo + Silero VAD + fin de frase adaptativo. |
| `voz/voice_id.py` | Identidad por voz (WeSpeaker, embeddings). |
| `voz/jev_decision.py` | Decisiones de turno con JEV: ruta y emoción. |
| `llm/llm_router.py` | Rotación de LLM por cupo (Groq / Ollama). |
| `asistente/assistant.py` | Hora, fecha, clima y noticias, inyectados al prompt. |
| `asistente/reminders.py` | Recordatorios y alarmas. |
| `asistente/music.py` | Control de Spotify por voz. |
| `asistente/busqueda.py` | Búsqueda web del modo ingeniero (Serper). |
| `agente/modo_dueno.py` | Comandos de la voz del dueño y modo ingeniero. |
| `agente/claude_code.py` | Sesión de Claude Code del modo ingeniero. |
| `memoria/memory.py` | Memoria persistente (cara↔nombre + voz, SQLite). |
| `memoria/memoria_admin.py` | CLI para ver y editar la memoria (`python -m memoria.memoria_admin`). |

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
python -m hardware.discovery       # refresca IPs de los ESP32 (tras cambiar de WiFi)
python supervisor.py      # sistema completo, relanzado si se cae (Q para salir)
python main.py            # lo mismo, sin relanzar
# o con el CLI, desde la raíz del repo: bob   (Bob de mentira sin placas: bob --falso)

python tests/hardware/test_1_serial.py        # COM3 / OLED / servos / throttle
python tests/hardware/test_2_tracker.py       # stream MJPEG + MediaPipe + servos (sin voz)
python tests/hardware/test_2b_stream_only.py  # diagnóstico ESP32-CAM aislado
python tests/hardware/test_3_voice.py         # mic + Whisper + Llama + TTS + OLED
python tests/hardware/test_5_integration.py   # integración
python tests/hardware/test_6_multipersona.py  # varias personas (escenario feria)
python tests/hardware/test_memoria.py         # memoria persistente
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

---

## 9. CLI de Bob (`bob-cli/`)

CLI al estilo de Claude Code: muestra todo lo que Bob escribe, con una barra de estado en
vivo y los atajos. Es opcional: `python main.py` (modo clásico) no cambia y es el plan B.

```
bob.cmd → bob-cli (Node, Ink) ──lanza──▶ robot_bob/main.py con BOB_CLI=1
             ▲  stdout: NDJSON (un evento por línea)  │
             └──────────────────────────────────────┘
             ──▶ stdin: comandos {"cmd": "tecla" | "salir", ...}
```

- **Eventos** (`nucleo/puente_cli.py`):
  - `{"t":"log","origen","nivel","ansi","turno"?}`: cada línea que escribe Bob, con los colores de Rich. `nivel` sale de `errores.error`/`aviso`; `turno` marca «Usuario:» y las frases de Bob (`puente_cli.turno()`).
  - `{"t":"estado",…}`: lo que cambió de la FSM, la emoción, el micrófono, la música, el baile o el modo ingeniero (`estado_cli`).
  - Lo que no es JSON y el stderr nativo se muestran crudos (en gris).
- **Captura sin tocar los mensajes:** con `BOB_CLI=1`, `sys.stdout` y `sys.stderr` pasan a ser escritores de eventos. Rich busca `sys.stdout` al escribir, así que las consolas de Rich y los `print()` quedan capturados sin cambiarlos.
- **Atajos:** Q, S, D, J, P, T, 1 a 4 (modos expresivos, `nucleo/modos.py`), E y ENTER llegan como `{"cmd":"tecla"}` a una cola que el loop de `main.py` lee cuando `cv2.waitKey` no trae nada. Pasan por las mismas ramas que en la ventana de video. Q marca `teclas_cli.salida`, así que funciona aunque no llegue video. E trae el nombre escrito en el CLI.
- **Cierre:** si se cierra el stdin de Bob (el CLI terminó o se cayó), Bob cierra limpio: `salir` → el `finally` de `main.py`. Si el CLI cae, Bob no queda corriendo con COM3 tomado. Si Bob se cae, el CLI queda abierto con el código de salida.
- **Trampas de Windows, ya resueltas:**
  - Un hilo bloqueado leyendo el pipe de stdin traba a cualquier otro que consulte ese handle, y la carga de la DLL de Fortran de scipy se colgaba. Por eso el lector usa una copia (`os.dup`) y fd 0 pasa a NUL.
  - Con la salida por pipe, Rich baja a 16 colores ("legacy windows"). En modo CLI se desactiva esa detección.
  - `signal.raise_signal(SIGINT)` despierta un `time.sleep` del hilo principal; `_thread.interrupt_main` no.
- **Pruebas:**
  - Python: `test_puente_cli`, `test_teclas_cli`, `test_estado_cli`, `test_serial_simulado` y `test_lector_webcam` (unit).
  - CLI: `npm test` y `npm run typecheck` en `bob-cli/`.
  - Sin placas: `bob --falso` (modos `demo`, `rafaga [s]` y `caida`).
  - Sin ESP32: `bob --alone`. Webcam y micro de la laptop; la cabeza y la cara van a la barra del CLI (`SerialSimulado`).
