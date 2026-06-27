# Bob — Catálogo de Features (filtrado a 100% viable)

Bob es un robot social impulsado por IA: visión, voz, memoria, emociones y comportamiento
autónomo. El cerebro corre en la **laptop** (MediaPipe + Groq); el ESP32 solo hace
servos pan/tilt + OLED + audio.

> Esta lista quedó **podada a features con ~100% de éxito** dadas las limitaciones reales
> (ver apéndice "Descartado"). Todo lo que sobrevive corre en la laptop o usa hardware que
> ya tenemos y funciona. Orden = recomendación de implementación (olas de software primero).

---

## Funcionalidades Actuales (ya funcionando)

**Visión:** reconocimiento facial · presencia humana · seguimiento Pan/Tilt · búsqueda activa.
**Conversación:** LLM (Groq) · inicio por cara o wake word · respuestas por voz · captura por mic.
**Personalidad:** sistema de emociones · emociones persistentes en la charla (Feliz/Triste/Enojado/Neutral).
**Expresión visual:** OLED de ojos · expresiones emocionales · animaciones de sueño y de charla.
**Autónomo:** modo sueño · despertar automático · búsqueda de personas · inicio autónomo de charla.

---

# Objetivos de Desarrollo (solo features 100% viables)

Orden recomendado: **P1 → P2 → P3 → P7 → P9 → P5 → P6 → P4 → P8 → P10**
(las primeras son software puro sobre el cerebro de la laptop; las últimas dependen de
CPU/visión/firmware pero siguen siendo 100% alcanzables con el alcance ya recortado).

### Estado de avance (2026-06-26)

| Prioridad | Estado |
|---|---|
| **P1 Memoria persistente** | ✅ hecho (cara↔nombre, SQLite, integrada, `memoria_admin`) |
| **P2 Relaciones sociales** | ✅ hecho (amistad/confianza/historial) |
| **"Actitud"** (soliloquio + muecas, out-of-chat) | ✅ hecho (ver [FEATURES.md](FEATURES.md)) |
| Red / portabilidad (discovery + mDNS + multi-WiFi) | ✅ hecho (ver [NETWORK.md](NETWORK.md)) |
| **P3 Expresividad** | ✅ hecho — estados internos (Energía/Motivación/Curiosidad/Sociabilidad) que derivan y sesgan tono/iniciativa + caras OLED nuevas (emocionado/asustado/avergonzado) en firmware + todos los tags EMO habilitados |
| **P7 Autónomo avanzado** | ✅ hecho — el opener retoma temas de charlas pasadas (memoria P1) vía LLM |
| **P9 Asistente personal** | ✅ hecho — info inmediata (hora/fecha/clima vía wttr.in) por intent+inyección + recordatorios/temporizadores/alarmas con scheduler local y disparo proactivo (reusa el camino de soliloquio). Pendiente opcional: noticias (API). |
| P5 Juegos · P6 Música · P4 Visión · P8 Físico · P10 Dev | ❌ pendiente |

> **Foco actual:** P9 (Asistente personal) **hecho** — info inmediata
> (hora/fecha/clima) + recordatorios/temporizadores proactivos, vía **detección de
> intención + inyección al system prompt** (no tool-calling nativo): funciona con
> los 3 backends incluido el LLM local chico y respeta el TTS por frase. Audio del
> speaker afinado (ver `genesis/decisions/ADR-001` para el salto I2S pendiente).
> Siguiente prioridad: **P5 (Juegos)** — adivinanzas/trivia (LLM) +
> piedra-papel-tijera (MediaPipe Hands).

## Prioridad 1 — Memoria Persistente

### Reconocimiento de Personas (usuarios enrolados, de cerca)
* Asociar cara ↔ nombre.
* Reconocer usuarios conocidos.
* Registrar nuevos usuarios.

### Memoria Personal (DB local)
Guardar: Nombre · Edad · Gustos · Temas favoritos · Fecha de última interacción · Relación con Bob.

### Memoria Episódica
Guardar recuerdos importantes (conversaciones relevantes, eventos, experiencias) que el
LLM resume y luego reinyecta al contexto.
Ej: *"Recuerdo cuando me hablaste de tu proyecto."*

## Prioridad 2 — Relaciones Sociales

### Sistema de Amistad
Cada usuario tendrá: nivel de amistad · nivel de confianza · historial de interacciones.

### Comportamiento Adaptativo
Bob modifica según el usuario: forma de hablar · entusiasmo · frecuencia de saludo · temas sugeridos.

## Prioridad 3 — Expresividad

### Nuevas Emociones (tags del LLM)
Curiosidad · Sorpresa · Vergüenza · Orgullo · Aburrimiento · Entusiasmo.

### Estados Internos (variables del StateMachine)
Energía · Motivación · Curiosidad · Sociabilidad.

### Expresiones OLED (firmware editable)
Ojos curiosos · confundidos · emocionados · aburridos · asustados.

## Prioridad 7 — Comportamiento Autónomo Avanzado

### Actividades Autónomas (estando solo)
Explorar visualmente · mirar alrededor · bostezar · buscar personas.
*(Movimientos suaves por la limitación de poder — ver apéndice.)*

### Conversaciones Autónomas (sobre P1)
Iniciar conversaciones · hacer preguntas · recordar temas pendientes.
Ej: *"La última vez hablábamos de tu robot, ¿cómo va?"*

## Prioridad 9 — Asistente Personal

### Información
Hora · Fecha (local) · Clima · Noticias (vía API).

### Productividad
Alarmas · Recordatorios · Temporizadores · Agenda (scheduling local + tool-calling del LLM).

## Prioridad 5 — Juegos

### Juegos Conversacionales (LLM)
Adivinanzas · Trivia · Veo-veo.

### Juegos Físicos (viables)
Piedra-papel-tijera (detección de mano) · Seguir movimientos (pan/tilt sigue al usuario).

## Prioridad 6 — Entretenimiento y Música (control)

### Música (reproducción en la laptop)
Reproducir · Pausar · Cambiar canción · Ajustar volumen.

### Entretenimiento (LLM)
Contar chistes · Contar historias · Hacer preguntas divertidas.

## Prioridad 4 — Visión Artificial Avanzada (modelos confiables)

### Detección de Gestos de Mano (MediaPipe Hands, de cerca)
Pulgar arriba · Pulgar abajo · Piedra · Papel · Tijera.

### Reconocimiento de Objetos (YOLO, clases COCO)
Teléfono · Computadora · Libro · Botella · Mochila.

### Códigos QR (pyzbar)
Lectura de códigos QR.

## Prioridad 8 — Interacción Física (2 DOF) y Reacciones

### Gestos de Cabeza (pan/tilt)
Asentir · Negar · Inclinar la cabeza.

### Reacciones (audio)
Reaccionar a aplausos · a sonidos fuertes (detección por RMS del mic).

### Simulación de Daño (por comando de voz: "bang" / "te disparo")
Ojos X · desmayo temporal (OLED + cabeza caída) · reinicio (`machine.reset`) · comentarios humorísticos.

## Prioridad 10 — Modo Desarrollador

### Diagnóstico
Laptop: CPU · RAM · Temperatura (psutil). ESP32: estado WiFi · sensores (requiere canal de
telemetría de retorno en el firmware).

### Debug
Logs · Reinicio remoto del ESP32 · estado de servicios.

---

# Objetivo Final

Bob como entidad social que **reconoce personas, mantiene relaciones de largo plazo,
recuerda experiencias, expresa emociones, inicia conversaciones y aprende de sus
interacciones** — generando sensación de personalidad propia. No un chatbot: una presencia
social con memoria, emociones e iniciativa. La presencia **física** se expresa con una
cabeza de 2 ejes (pan/tilt) + ojos OLED.

---

# Apéndice — Descartado por limitaciones (no llega a 100%)

| Feature | Por qué se descarta |
|---|---|
| Detección de emociones humanas (sonrisa/tristeza/enojo…) | Precisión baja a VGA + luz de feria; no confiable. |
| Gestos dinámicos: saludo con mano / chocar los cinco (detección) | Detección de movimiento poco confiable. |
| OCR de texto y nombres | Letra chica ilegible a VGA (firmware CAM fijo). *QR sí queda.* |
| Chocar los cinco (físico) · Saludar con brazo | **Bob no tiene brazos** (solo pan/tilt). |
| Bailar al ritmo / coreografías / animaciones de baile | 2 DOF + **brownout** de la fuente con movimiento continuo. |
| Música por el parlante del robot | Audio 8 kHz u8 pésimo. *Control + reproducción en laptop sí queda.* |
| Actualizaciones OTA del firmware | Esfuerzo alto, fiabilidad no garantizada. |

> Estos vuelven a la mesa si se resuelven sus límites: **fuente ≥2 A** (baile/movimiento),
> **brazos/servos extra** (gestos físicos), o **cámara de mayor resolución** (OCR/emociones).

---

# Mejoras futuras (funcionan pero se pueden pulir) — no bloquean la demo

## Voz (TTS)

**Estado:** edge-tts `es-BO-MarceloNeural` + prosodia por emoción (`TTS_EMO_PROSODY`),
estilo caricaturesco. Funciona y suena menos plano, pero sigue siendo voz neural.

Para mejorar:
- Motor más humano si hay presupuesto/setup: **ElevenLabs** (la más natural, cupo gratis
  chico para feria continua) o **Piper** (local, offline, gratis — evaluar voces es_*).
- Afinar `TTS_EMO_PROSODY` con pruebas reales (¿muy caricaturesco? ¿muy rápido?).
- Probar `es-BO-SofiaNeural` u otras voces y comparar A/B.
- Para el parlante 8 kHz u8: revisar `TTS_FFMPEG_FILTERS` para que la voz aguda no
  sature/aliasee al bajar a 8 bits (hoy se usa el parlante de la laptop).
- Variar `volume`/énfasis por emoción además de rate/pitch (más expresividad).
