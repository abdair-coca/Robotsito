# Robot Bob — Contexto para Agentes LLM

Archivo de contexto para que cualquier LLM pueda trabajar en este proyecto sin
descubrir todo desde cero. **El sistema actual y canónico es `robot_bob/`.** Todo
lo demás en el repo es base histórica de la que nació Bob — ver §8.

> Última actualización: 2026-06-19. Si modificas arquitectura o config, actualiza
> este archivo y corre `graphify update .` para mantener el grafo al día.
> (Plan de locomoción con motores DC: §10.)

---

## 1. Qué es

**Bob** es un robot interactivo de feria desarrollado por **Abdair** (estudiante de
Ingeniería Informática, Universidad Autónoma Tomás Frías — UATF, Potosí, Bolivia).
Reconoce caras, mueve la cabeza (pan/tilt) para seguirte, muestra emociones en una
pantalla OLED y conversa por voz en español usando IA en la nube.

Objetivo de diseño: que alguien que hable con Bob 30 s sienta que **lo entiende
emocionalmente**, no que procesa lenguaje. Compañero, no asistente. Detalle del
sistema emocional en `robot_bob/InteractiveGoal.md`; etapas de construcción en
`robot_bob/AgentsGoal.md`.

---

## 2. Hardware

| Componente | Ubicación | Función |
|---|---|---|
| ESP32 DevKit | COM3 / IP `192.168.0.23` | OLED SH1106 (I2C **32/33**), servos pan/tilt (GPIO13/12), mic MAX9814 (GPIO34), speaker PAM8403 (GPIO25), motores DC L298N IN1-4 (GPIO19/21/22/23) |
| ESP32-CAM | IP `192.168.0.22` | Stream MJPEG en `:81/stream` |
| Laptop | local | Toda la lógica Python (visión, IA, audio) |

El DevKit expone dos transportes: **USB COM3** y **servidor TCP de control (puerto 5007)**.

### Firmware del dispositivo — `../Esp32/`
MicroPython que corre **dentro** del ESP32 DevKit (subir con Thonny/ampy). No es
código de la laptop. Archivos:

| Archivo | Rol |
|---|---|
| `Esp32/main.py` | Firmware completo: WiFi, 2 hilos (`hilo_audio` half-duplex 8 kHz, `hilo_oled` ~10 fps) + loop de control. Bindea `5005`/`5006` (audio) y `5007` (control). Parser de texto compartido WiFi+USB (`aplicar_cmd`). |
| `Esp32/oled_ojos.py` / `.mpy` | Motor de ojos emocionales (tick/blink/wink, microsacadas, sleepy, pupilas SIGUIENDO, boca HABLANDO). El `.mpy` es la versión compilada que se sube; recompilar tras editar el `.py`. |
| `Esp32/sh1106.py` | Driver del OLED SH1106. |
| `Esp32/prueba_oled.py` | Prueba aislada del OLED. |
| `Esp32/config.py` | **No está en el repo** (gitignoreado, vive en el dispositivo). Define `SSID`, `PASSWORD` y, opcionalmente, `STATIC_IP`/`GATEWAY`/`SUBNET`/`DNS`. |

El protocolo de texto del puerto 5007 es **idéntico** al serial USB:
`H:<pan>,V:<tilt>\n` (servos), `ESTADO:<NOMBRE>\n` (OLED), `SIGUIENDO:<dx>,<dy>\n`,
`M:<izq>,<der>\n` (motores DC, valores en {-1,0,1}).
La laptop solo escribe, nunca lee — el firmware no responde por TCP a propósito.

> **IP estática (clave para WiFi):** el firmware fija `STATIC_IP` antes de conectar
> si el `config.py` del dispositivo la define. Para que `CONTROL_IP=192.168.0.23`
> (laptop) siempre encuentre al DevKit, agregar en `Esp32/config.py` del dispositivo:
> `STATIC_IP='192.168.0.23'`, `GATEWAY='192.168.0.1'`, `SUBNET='255.255.255.0'`,
> `DNS='8.8.8.8'`. Sin esto el DevKit toma IP por DHCP (puede cambiar) y el control WiFi falla.

### Puertos TCP
- `5005` — ESP32 → laptop: stream continuo del micrófono (solo si `USE_ROBOT_MIC`).
- `5006` — laptop → ESP32: audio TTS + comandos STOP/KEEPALIVE (solo si `USE_ROBOT_SPEAKER`).
- `5007` — laptop → ESP32: servidor de control de servos + OLED por WiFi (`USE_WIFI_SERIAL`).
- `:81/stream` — ESP32-CAM: video MJPEG.

---

## 3. Estado actual y foco (2026-06-18)

- **Demo de feria funcional.** Sistema integrado (visión + voz + emociones) corre y es presentable.
- **🚧 Foco activo: migración cable → WiFi.** Se quiere que servos/OLED y audio
  funcionen por WiFi en vez de USB. El código ya está listo para WiFi
  (`USE_WIFI_SERIAL`, `CONTROL_IP`, `CONTROL_PORT=5007` en `config.py`;
  `serial_manager._conectar_wifi`), **pero la conexión WiFi todavía falla** — es el
  problema que se está depurando. **La arquitectura por cable (USB COM3) DEBE
  mantenerse como fallback** y nunca eliminarse.
  - `SerialManager` resuelve transporte en orden: WiFi (5007) → USB COM3 → "sin ESP32" (solo visión).
  - **Causa #1 del fallo WiFi = IP por DHCP.** El firmware no fijaba IP estática, así que
    el DevKit podía tomar una IP ≠ `192.168.0.23` y la laptop no lo encontraba. **Fix
    aplicado:** `Esp32/main.py` ahora fija `STATIC_IP` si el `config.py` del dispositivo
    la define (ver §2). Falta agregar esas 4 líneas al `config.py` del ESP32 y re-subir el firmware.
  - Otras causas a descartar: AP/client isolation del router (aísla laptop ↔ ESP32),
    SSID/PASSWORD incorrectos en `Esp32/config.py`, o `conectar_wifi()` lanzando
    `RuntimeError` a los 15 s (el firmware muere sin levantar el servidor 5007).
  - Para confirmar la IP real: abrir Thonny por USB y ver el print de arranque
    `Conectado! IP del ESP32: ...`.

---

## 4. Arquitectura — hilos del proceso `robot_bob/main.py`

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

**Orden de arranque (main.py):** SerialManager → StateMachine → FacialTracker
(espera primer frame, máx 15 s) → VoicePipeline + wake monitor → BehaviorEngine → loop.
Cierre limpio con Q: posición home + estado neutro, todos los hilos son daemon.

### Módulos en `robot_bob/`
| Archivo | Rol |
|---|---|
| `main.py` | Orquestador / entrada. |
| `config.py` | **TODA** la config modificable (hardware, IPs, audio, conversación, servos, LLM/TTS, prompt). Gitignoreado. |
| `serial_manager.py` | Único dueño del canal de control. Cola priorizada SERVO > ESTADO > SIGUIENDO, throttle, dedup. WiFi/USB/none. |
| `state_machine.py` | Máquina de estados central + mood drift emocional. |
| `facial_tracker.py` | `LectorStream` (socket MJPEG) + `DetectorFacial` (MediaPipe). Delega al SerialManager. |
| `voice_pipeline.py` | STT (Whisper) → LLM (Llama) → TTS (edge-tts). Wake word, barge-in, multi-turno, parser de tags `[EMO:X]`. |
| `behavior.py` | Motor de movimiento humano @20 Hz. |
| `expression_engine.py` | Reacciones instantáneas a lo que dice el usuario (`react_to_user_text`). |

### Máquina de estados
```
IDLE → PRESENCE → LISTENING → THINKING → SPEAKING → CONVERSATION_IDLE → (IDLE)
```
- **IDLE**: sin cara, movimientos aleatorios "aburridos".
- **PRESENCE**: cara detectada, timer de permanencia; manda `SIGUIENDO` al OLED.
- **LISTENING**: grabando (gatillado por permanencia+probabilidad o wake word "Bob").
- **THINKING**: STT + LLM procesando, cabeza casi quieta.
- **SPEAKING**: reproduce TTS, con barge-in (interrupción por voz) habilitado.
- **CONVERSATION_IDLE**: entre turnos; timeout → vuelve a IDLE.

---

## 5. Sistema emocional (3 capas)

1. **Mood drift** — sentimiento base acumulativo *dentro* de la conversación
   (`mood: float` en `StateMachine`, clamp `[-1,1]`, decay 5%/turno, reset al terminar).
2. **Instant reaction** — flash de ojos a lo que dice el usuario, antes de responder
   (`expression_engine.react_to_user_text`): amor, risa, confusión, insulto→triste→confundido, etc.
3. **Expression tag por frase** — el LLM prefija cada frase con `[EMO:X]`; el parser
   en `voice_pipeline` lo extrae antes del TTS y lo manda al OLED.

Vocabulario de tags (subconjunto de las 21 emociones del firmware OLED):
`FELIZ`, `MUY_FELIZ`, `CURIOSO`, `TRAVIESO`, `PENSANDO`, `SORPRENDIDO`,
`CONFUNDIDO`, `TRISTE`, `AMOR`, `HABLANDO` (default si no hay tag).

El formato `[EMO:X]` es **obligatorio** y está definido en `SYSTEM_PROMPT` (config.py).
Detalle completo: `robot_bob/InteractiveGoal.md`.

---

## 6. Backends de IA (Groq + edge-tts)

| Función | Backend / modelo |
|---|---|
| STT | Groq Whisper `whisper-large-v3-turbo` |
| LLM | Groq `llama-3.3-70b-versatile`, temp 0.8, máx 160 tokens |
| TTS | `edge-tts` voz `es-MX-JorgeNeural` → ffmpeg → uint8 8 kHz |
| VAD | `webrtcvad-wheels` con gate de ruido adaptativo |
| Wake word | "bob" / "hola bob" / "hey bob"… vía Whisper + fuzzy match |

Audio actual: **mic y speaker de la laptop** (`USE_ROBOT_MIC=False`,
`USE_ROBOT_SPEAKER=False`) por estabilidad. Si se activan, `main.py` conecta el
`AudioIO` TCP del ESP32 (importado de `voicechatLap/`); si el TCP falla cae a laptop.

### Secretos — `.env` (no commitear)
La `GROQ_API_KEY` **ya no vive en el código**. `config.py` la lee del entorno con un
cargador `.env` manual (sin dependencias extra):
- `robot_bob/.env` — clave real, **gitignoreado**.
- `robot_bob/.env.example` — plantilla rastreada. Para un setup nuevo: `cp .env.example .env` y rellenar.
- Obtener clave en https://console.groq.com/keys

> Nota: `config.py` está gitignoreado y la key **nunca** estuvo en el historial de git,
> así que no hubo filtración. Rotarla es opcional, no urgente.

---

## 7. Cómo correr y testear

Entorno: **Python 3.11**, venv en `robot_bob/venv311/`. Deps en `robot_bob/requirements.txt`
(`opencv-python`, `mediapipe`, `numpy`, `pyserial`, `webrtcvad-wheels`, `sounddevice`,
`scipy`, `edge-tts`, `imageio-ffmpeg`, `groq`, `pygame`, `rich`).

```bash
# desde robot_bob/, con venv311 activo
python main.py            # sistema completo (Q para salir)
python tests/test_1_serial.py        # COM3 / OLED / servos / throttle
python tests/test_2_tracker.py       # stream MJPEG + MediaPipe + servos (sin voz)
python tests/test_2b_stream_only.py  # diagnóstico ESP32-CAM aislado
python tests/test_3_voice.py         # mic + Whisper + Llama + TTS + OLED
python tests/test_4_behavior.py      # behavior engine
python tests/test_5_integration.py   # integración
python tests/test_6_multipersona.py  # varias personas (escenario feria)
```

> **Existen skills de Claude Code para el flujo guiado** — preferilos cuando apliquen:
> `bob-run` (arranque/cierre limpio), `bob-test` (tests aislados en orden, con validación
> entre cada uno), `bob-diagnose` (diagnóstico: stream colgado, COM3 ocupado, voz muda,
> servos bruscos, OLED no actualiza), `bob-add-state` (agregar estado nuevo a la FSM+OLED).

**Conflicto crítico conocido:** dos procesos no pueden abrir COM3 a la vez →
`SerialException`. `SerialManager` es el único dueño; nunca llames `serial.write()` directo.

---

## 8. Carpetas legacy / base — NO TOCAR salvo pedido explícito

Estas carpetas son la base histórica de la que se construyó `robot_bob/`. No las
edites a menos que el usuario lo pida. **Atención:** algunas siguen siendo
**dependencias vivas** de robot_bob (se importan en runtime), aunque no se editen:

| Carpeta | Estado | Nota |
|---|---|---|
| `voicechatLap/` | **dep viva** | robot_bob importa `audio_io` (AudioIO TCP), `wake_word`, y config base. |
| `Esp32ThinkerAICam/` | **dep viva** | aporta el modelo `blaze_face_short_range.tflite` que carga el tracker. |
| `Esp32/` | **firmware vivo** | MicroPython del DevKit (OLED + servos + audio TCP + control 5007). |
| `Oled1_3/` | base / pruebas OLED | experimentos de ojos/caritas para el firmware. |
| `VoiceChat/` | legacy | chat de voz solo-hardware del robot (versión previa). |
| `IAConversation/`, `AudioMAXpam/`, `MotorPrueba/` | legacy | prototipos iniciales (IA, audio, motores). |

`tiene su propio .gitignore que ignora venv/, __pycache__, config.py y .env.`

---

## 9. Convenciones

- **Idioma:** código, comentarios y docs en **español**. Mantenlo.
- **Commits:** usar el skill `my-commit` — usuario `abdair-coca` / `cocaabdair@gmail.com`,
  formato Conventional Commits, **sin** línea `Co-Authored-By`.
- **Config centralizada:** todo lo ajustable va en `robot_bob/config.py`, no hardcodear en módulos.
- **Knowledge graph:** existe `graphify-out/`. Para preguntas de arquitectura usar
  `graphify query "..."` / `graphify path "A" "B"` / `graphify explain "concepto"`.
  Tras modificar código, `graphify update .` (AST, sin costo de API).
- **Secretos:** nunca hardcodear claves; van en `.env` (ver §6).

---

## 10. PLAN — Locomoción con motores DC (pendiente de implementar)

Objetivo: que Bob **se desplace** (no solo mueva la cabeza pan/tilt). Tracción
diferencial (tipo tanque) con **2 motores DC** vía un **puente H dual (L298N o similar)**.

> **Estado (2026-06-19):** Fases 0–3 ✅ validadas en vivo.
> - **Fase 0:** OLED en I2C 32/33 (21/22 libres); motores con cargador aparte, GND común; ENA/ENB jumpeados (sin velocidad).
> - **Fase 1 (firmware):** `mover_motores()`, comando `M:<izq>,<der>`, watchdog 400 ms. ✅ probado en Thonny.
> - **Fase 2 (laptop):** `SerialManager.cmd_motor()` + `tests/test_motor.py` (teclado WASD, reenvía cada 150 ms). ✅ probado.
> - **Fase 3 (autónomo):** `behavior._maybe_girar_cuerpo` — Bob **gira sobre su eje** (no traslada)
>   en ráfagas cortas (`GIRO_BURST_S`) + cooldown cuando el pan se satura y la cara sigue corrida.
>   Config: `MOTORES_ENABLED`, `GIRO_*`, `GIRO_INVERTIR` (flip si gira al lado equivocado).
> - **Pendiente:** control de velocidad (ENA/ENB + PWM), y traslación solo con sensores (no hay aún).

### Cableado dado por el usuario
| Señal | Pin ESP32 | Driver |
|---|---|---|
| IN1 | GPIO19 | dirección motor izquierdo (A) |
| IN2 | GPIO21 | dirección motor izquierdo (A) |
| IN3 | GPIO22 | dirección motor derecho (B) |
| IN4 | GPIO23 | dirección motor derecho (B) |
| GND | GND | tierra común |

- Motor izquierdo → OUT1 / OUT2 (lo manejan IN1/IN2).
- Motor derecho → OUT3 / OUT4 (lo manejan IN3/IN4).
- Tabla de verdad por motor: `IN_a=1,IN_b=0`→adelante · `0,1`→atrás · `0,0`→libre (stop) · `1,1`→freno.

### ⚠️ Bloqueantes a resolver ANTES de codear (Fase 0)

1. **Conflicto de pines OLED ↔ motor.** GPIO21 y GPIO22 ya son el **I2C del OLED
   SH1106** (§2). No se pueden usar para IN2/IN3 con el OLED conectado ahí. Opciones:
   - **(A)** Mover el OLED a otros pines I2C libres (ej. SDA=GPIO32, SCL=GPIO33) y
     actualizar el `I2C(...)` del firmware + recablear. (Recomendado: deja el cableado
     de motores como lo hizo el usuario.)
   - **(B)** Mover IN2/IN3 a GPIOs libres (ej. 18, 5, 17, 16) y recablear los motores.
   - Decisión del usuario. **Sin esto, OLED y motores no funcionan juntos.**
2. **Alimentación separada (brownout).** Los motores DC tiran mucha más corriente que
   los servos (picos de arranque/stall). **Deben alimentarse de una fuente/batería
   aparte** (no del 5V del ESP32), con **GND común**. Con el brownout que ya hay por
   servos+WiFi (ver §3 / Objetivo 2), motores sobre el riel del ESP32 = reset seguro.
3. **ENA/ENB (velocidad).** El usuario dio solo IN1–IN4. El L298N tiene ENA/ENB:
   - Si están **jumpeados a HIGH** → solo on/off (adelante/atrás/girar/parar, **sin
     control de velocidad**). Plan base asume esto.
   - Para **velocidad variable** hacen falta 2 pines PWM más (ENA, ENB). Extensión opcional.

### Arquitectura de software (sigue el patrón existente)

Reusar el canal de control de texto (USB COM3 / TCP 5007) y el `SerialManager` como
**único dueño**. NO abrir los pines desde la laptop — todo va por el firmware.

1. **Firmware `Esp32/main.py`:**
   - Inicializar los 4 pines como `Pin(..., Pin.OUT)`.
   - `mover_motores(izq, der)` con `izq,der ∈ {-1,0,1}` → setea IN1–IN4 por la tabla de verdad.
   - Comando nuevo en el parser `aplicar_cmd` (mismo formato de texto que `H:`/`ESTADO:`):
     `M:<izq>,<der>\n` (ej. `M:1,1`=adelante, `M:-1,-1`=atrás, `M:1,-1`=giro, `M:0,0`=stop).
   - **Watchdog de seguridad:** si no llega comando de motor en ~400 ms, **parar los
     motores** (evita que Bob se escape si se corta la conexión WiFi/USB). Crítico.
   - Parar motores al boot y en cualquier error.
2. **`robot_bob/serial_manager.py`:** agregar `cmd_motor(izq, der)` con su prioridad en
   la cola priorizada (sugerido: MOTOR alta, junto a SERVO) + throttle. Mismo patrón que
   `cmd_servo`/`cmd_estado`. Enviar `M:<izq>,<der>\n`.
3. **Control (nuevo `robot_bob/locomotion.py` o dentro de `behavior.py`):** decide cuándo
   moverse. Arrancar con **control manual por teclado** (test), luego comportamientos:
   acercarse a una cara lejana, deambular en IDLE, retroceder si la cara está muy cerca.
4. **`config.py`:** `MOTORES_ENABLED` (default False hasta validar), velocidades, watchdog
   timeout, y los pines documentados (los pines reales viven en el firmware).

### Fases (gateadas — test + feedback entre cada una)

- **Fase 0:** Resolver pines (OLED vs motor) + fuente separada para motores. **Bloqueante.**
- **Fase 1 (firmware):** `mover_motores()` + comando `M:` en el parser + watchdog. Test en
  Thonny: mandar `M:1,1`/`M:0,0` con las **ruedas en el aire** (sin tocar piso) y ver giro/parada.
- **Fase 2 (laptop):** `SerialManager.cmd_motor` + script de teclado (`tests/test_motor.py`)
  para manejar a Bob manualmente. Validar dirección de cada rueda (corregir signos si va al revés).
- **Fase 3 (integración):** comportamiento autónomo mínimo (acercarse / deambular) en `behavior.py`.
- **Fase 4 (pulido):** rampas de arranque/parada para no tironear; giros suaves; parar al cerrar
  (`main.py` finally) y al entrar en sueño.

### Seguridad (no opcional)
- Watchdog que para si no hay comandos.
- Parar motores en el `finally` de `main.py` y al cerrar `SerialManager`.
- Probar siempre primero con las ruedas levantadas del piso.
- Límite de velocidad/tiempo de marcha para que no se aleje de la laptop (depende de WiFi/USB).

> Nota: esta capacidad **levanta el techo de "presencia física"** que hoy limita varias
> features de `featuresGoal.md` (baile, seguir movimientos, etc.). Pero arrastra el mismo
> riesgo de **brownout** que ya es el cuello de botella del proyecto — la fuente separada
> para motores es condición para que esto sea viable.
