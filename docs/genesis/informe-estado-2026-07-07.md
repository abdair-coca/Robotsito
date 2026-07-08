# Informe de Estado — Robot Bob (Proyecto Génesis)

**Fecha:** 7 de julio de 2026
**Fase activa:** Fase 1 — "El alma de Bob" (Jun 2026 – Dic 2026)
**Hito reciente:** Presentación pública en feria — completada ✓
**Repositorio:** github.com/abdair-coca/Robotsito (106 commits)

---

## 1. Resumen ejecutivo

Bob es un robot humanoide de escritorio construido con ~28 USD (~280 BOB) en
hardware conseguido en Potosí, que hoy: ve, reconoce y recuerda personas;
conversa por voz en español con personalidad propia; expresa emociones en
ojos OLED; sigue caras con la cabeza y gira el cuerpo; reproduce música de
Spotify y baila; da hora, clima y noticias en vivo; gestiona recordatorios;
y ejecuta un show de presentación coreografiado activado por voz. Todo el
procesamiento pesado corre en una laptop; los ESP32 actúan como periferia
conectada por WiFi con fallback USB.

La demo pública se realizó con éxito sobre un hotspot de celular, con el
sistema completo funcionando en red desconocida gracias al autodiscovery.

---

## 2. Hardware (total ~28 USD / ~280 BOB)

| Componente | Rol | Estado |
|---|---|---|
| ESP32 DevKit v1 | Cerebro periférico: servos, OLED, motores, audio TCP | Integrado |
| ESP32-CAM | Cámara MJPEG 320×240 por WiFi | Integrado |
| SH1106 OLED 1.3" | Ojos animados con emociones | Integrado |
| MAX9814 | Micrófono del robot (opcional; hoy se usa mic laptop) | Integrado |
| PAM8403 + parlante | Voz del robot (DAC 8-bit @ 8 kHz) | Integrado |
| 2× servos | Cabeza pan/tilt | Integrado |
| L298N + 4 motores TT | Giro del cuerpo sobre su eje | Integrado |
| Cuerpo Otto DIY (impresión 3D) | Chasis (modelo FreeCAD + STLs en repo) | Integrado |

Red: las 3 placas por WiFi con IP dinámica; `discovery.py` las localiza
(escaneo de subred + mDNS) y cachea en `devices.json`. Cambiar de red =
correr un comando. Firmware DevKit con lista multi-WiFi (casa + hotspot).

## 3. Arquitectura de software (~7.600 líneas Python + firmware)

```
Laptop (Python 3.11, venv)                    ESP32 (periferia WiFi)
┌─────────────────────────────────┐           ┌──────────────────────┐
│ main.py — orquestador + HUD     │◄─ MJPEG ──┤ ESP32-CAM            │
│ ├ SerialManager (cola prio,     │── TCP ───►│ DevKit MicroPython:  │
│ │  WiFi→USB fallback, reconexión)│  5005-7   │  servos, OLED, motores│
│ ├ StateMachine (7 estados +     │           │  mic/speaker (DAC)   │
│ │  estados internos E/M/C/S)    │           └──────────────────────┘
│ ├ FacialTracker (MediaPipe)     │
│ ├ BehaviorEngine (20 Hz: idle,  │   Nube: Groq (Whisper STT + LLM 70B)
│ │  tracking, sueño, baile, show)│   edge-tts (voz) · Spotify · wttr.in
│ ├ VoicePipeline (wake→STT→LLM→  │   Local opcional: Ollama (sin internet)
│ │  TTS, 4 monitores en hilos)   │
│ └ show.py / music.py / memory.py│
│   assistant.py / reminders.py   │
└─────────────────────────────────┘
```

**Máquina de estados:** IDLE → PRESENCE → LISTENING → THINKING → SPEAKING →
CONVERSATION_IDLE, más estados internos continuos (energía, motivación,
curiosidad, sociabilidad) que modulan el tono del LLM y la iniciativa.

## 4. Capacidades logradas (Fase 1)

### Percepción
- **Seguimiento facial** MediaPipe a ~14 Hz sobre stream MJPEG, con predicción
  de velocidad, zona muerta, easing y suavizado EMA de servos.
- **Reconocimiento de personas** (InsightFace buffalo_l): embeddings faciales
  contra SQLite; 9 personas registradas. Enrolado en vivo con la tecla E sin
  congelar el video.
- **Escaneo activo**: al oír "Bob" sin ver a nadie, gira el cuerpo por ráfagas
  hasta encontrar al hablante (~360° máx).

### Conversación
- **Wake word "Bob"** siempre activa, con corte anticipado (dispara en ~1-1.5 s),
  gate de energía calibrable (`calibrar_mic.py`), fuzzy matching tolerante a
  errores de Whisper y escaneo de primeros 4 tokens.
- **Pipeline de voz**: Whisper (Groq) → LLM (llama-3.3-70b / Gemini / Ollama
  local con fallback automático) → edge-tts con prosodia emocional por tag
  [EMO:X]. Streaming: habla la primera frase mientras genera la segunda.
- **Personalidad**: system prompt propio, mood drift por conversación, rachas
  positivas, tags emocionales que mueven ojos y voz a la vez.
- **Memoria persistente**: reconoce a la persona al iniciar charla, inyecta su
  contexto (nombre, gustos, nivel de amistad, episodios previos) al prompt, y
  guarda recuerdos al despedirse. Relación evoluciona (desconocido → amigo).

### Expresión
- **Ojos OLED**: 7+ estados (esperando, escuchando, pensando, hablando, feliz,
  muy feliz, curioso, sospechando, siguiendo) con microexpresiones, muecas
  espontáneas y sincronización de boca durante TTS.
- **Soliloquios**: Bob habla solo cuando está aburrido o para romper el hielo
  (banco local + generación LLM con modelo chico 8B para ahorrar tokens).
- **Sueño**: tras 35 s solo se duerme (baja la cabeza, ojos cerrados); se
  despierta sobresaltado con la wake word.

### Música y baile
- **Spotify por voz**: play por tema, playlists por nombre, pausa, siguiente,
  volumen por porcentaje.
- **Baile**: coreografía de cabeza + contoneo sincronizado mientras suena
  música, con monitor que lo apaga solo al pausar (fail-safe si Spotify no
  responde). Durante música: cero muecas, cero soliloquios, cero conversaciones
  auto — solo obedece a la wake word.

### Asistente personal
- **Hora, fecha, clima** (wttr.in) y **noticias** (Google News RSS) inyectados
  al prompt solo cuando la intención lo pide (ahorro de tokens).
- **Recordatorios por voz**: "Bob, recordame X en N minutos" — anuncio
  proactivo al vencer, incluso en medio de otra charla.

### Show de presentación ("Bob, presentate")
Coreografía fija de ~75 s activada por voz: intro → tour de emociones →
movimiento suave de cabeza (25°/s) → giro de cuerpo → reconocimiento facial
en vivo (saluda por nombre) → recordatorios → hora/clima en vivo → cierre
bailando con Spotify. Cada paso degrada con gracia si su subsistema falla.
Fue la pieza central de la demo de feria.

## 5. Robustez para operación real (lo ganado esta semana)

| Problema encontrado | Solución |
|---|---|
| Speaker mudo tras la 1.ª frase (bug de firmware con envío por chunks) | TTS se envía completo de golpe (sendall) — validado con tests A/B de beeps |
| Robot mudo tras reboot/brownout del DevKit | Reconexión automática de audio con refresh de IP desde discovery |
| Volumen inaudible (TTS_VOLUME=0.1 olvidado) | 0.85 + documentado |
| Comandos serial reordenados (cola de prioridad sin FIFO) | Tiebreaker por contador |
| Logs ffmpeg basura por cada síntesis (FFREPORT mal seteado) | Corregido en raíz |
| Turno mudo con LLM local frío | Timeout 45 s para Ollama + cierre limpio de hilos |
| Cambio de red = reconfigurar IPs a mano | Cadena discovery → config → audio verificada de punta a punta |
| Ruido de feria | Perfil de config FERIA (VAD 3, gates, menos iniciativa) + calibrador de mic |

## 6. Ahorro de tokens / operación económica
- Historial recortado a 10 mensajes; máx 160 tokens y 2 frases por turno.
- Soliloquios con llama-3.1-8b-instant (libera TPM del 70B para conversación).
- Gate de energía evita llamadas Whisper con silencio/ruido.
- Contexto de asistente (clima/noticias) solo se inyecta si la intención lo pide.
- Backend local (Ollama) disponible como fallback sin internet ni costo.

## 7. Documentación y tooling del proyecto
- `docs/` técnico por módulo + `docs/genesis/` (fases, presupuesto, decisiones,
  experimentos) con skills de Claude Code para registrar avances y costos.
- Knowledge graph del código (graphify) para navegación asistida.
- Skills operativas: bob-run, bob-test, bob-diagnose, bob-add-state.
- Tests aislados por subsistema (serial, tracker, voz) + scripts de
  diagnóstico de audio y calibración de micrófono.

## 8. Deudas técnicas conocidas (registradas, no urgentes)
1. Bug de firmware del envío por chunks sin causa raíz (workaround sólido).
2. Cierre con Q en plena charla pierde el último recuerdo (carrera de hilos
   al cerrar la DB).
3. Enrolado por consola (`input()`) convive con prints de otros hilos.
4. ESP32-CAM: verificar credenciales de hotspot en su firmware Arduino
   (la demo usó el truco del hotspot con las placas ya asociadas).

---

*Informe generado al cierre de la presentación pública. La Fase 1 ("El alma
de Bob") está funcionalmente completa en sus objetivos centrales: memoria,
personalidad y reconocimiento de personas. Siguiente paso sugerido: registrar
este hito en la bitácora de fase (`/genesis-log`) y definir prioridades de la
Fase 2 ("El cuerpo").*
