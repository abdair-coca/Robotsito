# Bitácora — Fase 1: El alma de Bob

Entradas en orden cronológico. Una entrada por avance. Encabezado con fecha ISO.
Registrar con la skill `genesis-log`.

## 2026-06-25
**Tipo:** milestone
**Componente:** proyecto/genesis
**Descripción:** Inicio oficial del Proyecto Génesis. RobotCreeper evoluciona a humanoide de bajo costo con IA. Bob ya tiene face tracking (OpenCV/MediaPipe), pipeline STT→Groq→TTS, ojos OLED animados (SH1106), servo pan/tilt, cámara MJPEG, memory.db (SQLite), state_machine, behavior engine.
**Resultado:** Sistema de documentación Génesis creado. Base de Fase 1 establecida.
**Próximo paso:** Mejorar memoria persistente de Bob — que recuerde personas y contexto entre sesiones.

## 2026-06-25
**Tipo:** decision
**Componente:** voz/llm
**Descripción:** Evaluado LLM local (Ollama) para la charla, como alternativa sin tokens/internet a Groq. Benchmark de 4 modelos en la laptop (RTX 3050 4GB) con un harness propio (test_llm_qwen) midiendo latencia, tok/s y cumplimiento de formato (2 frases + tags [EMO:X]). qwen2.5:7b se siente más humano pero no entra en 4GB (CPU offload → 5 tok/s, inusable para voz). qwen2.5:3b es rápido pero desobediente y flojo de contenido. 7b q3_K_M sigue lento (8 tok/s) y fuga al chino.
**Resultado:** Elegido **llama3.2:3b** como cerebro local (41 tok/s en GPU, TTFT ~2.4s, esquiva temas sensibles y recuerda contexto mejor que qwen-3b); qwen2.5:3b queda de respaldo. Añadido un guard en voice_pipeline que fuerza el formato (corta a 2 frases e inyecta tags) pase lo que pase, más un prompt local estricto. Backend conmutable Ollama/Groq con fallback. Validado en charla en vivo (laptop, sin robot).
**Próximo paso:** Probar P7 end-to-end con el hardware completo (main.py).

## 2026-06-25
**Tipo:** milestone
**Componente:** voz/memoria
**Descripción:** Implementado P7 (conversación autónoma sobre P1): al reconocer a una persona con recuerdos en memoria, el opener deja de ser genérico y retoma un tema de una charla anterior vía LLM ("la última vez me hablabas de tu robot, ¿cómo va?"). Fallback al saludo por nombre si no hay recuerdos. Cubierto con test_7_opener_memoria aislado.
**Resultado:** P7 marcado como hecho en el ROADMAP. Bob ahora reconoce, recuerda y retoma temas — más cerca de "presencia social con memoria e iniciativa".
**Próximo paso:** P3 — estados internos (Energía/Motivación/Curiosidad/Sociabilidad) en la StateMachine. Caras OLED nuevas siguen diferidas (firmware).

## 2026-06-26
**Tipo:** fix
**Componente:** voz/audio (speaker)
**Descripción:** Mejorada la claridad del speaker (DAC interno 8-bit GPIO25 + amp PAM8403, salida telefónica 8 kHz). Laptop (`robot_bob/config.py`): `TTS_VOLUME` 0.50→0.85 (usa más rango del DAC 8-bit → menos ruido de cuantización), `TTS_PITCH_BASE` +20→+6 Hz y prosodia por emoción con el techo de pitch recortado ~14 Hz (los agudos altos sonaban metálicos en el speaker chico), cadena de filtros ffmpeg reescrita (highpass 180, banda de presencia 2200 Hz +3 dB para inteligibilidad de consonantes, lowpass 3400 anti-aliasing, alimiter 0.92). `voice_pipeline._mp3_a_u8`: añadido `-dither_method triangular` al bajar a 8 bits (grano áspero → hiss suave). Firmware (`firmware/Esp32/main.py` `play_mode`): quitado el re-anclado del reloj en CADA refill — metía un micro-salto de timing en cada borde de chunk (~128 ms) = zumbido/grano periódico; ahora reloj absoluto continuo, re-ancla solo si la red atrasa >50 ms.
**Resultado:** Voz más limpia, más fuerte y menos metálica/entrecortada dentro del techo del DAC 8-bit. Sintaxis laptop verificada (`py_compile`). Falta re-subir el firmware al DevKit y validar con `bob-test` voz.
**Próximo paso:** Re-flashear `firmware/Esp32/main.py` y A/B test. Para el salto real de calidad, ejecutar ADR-001 (I2S MAX98357A) cuando llegue el módulo.

## 2026-06-26
**Tipo:** decision
**Componente:** voz/audio (speaker)
**Descripción:** Documentada la decisión de migrar el speaker del DAC interno 8-bit + PAM8403 a un módulo **MAX98357A** (DAC + amp Class-D I2S, ~$3/20 BOB). El 8-bit interno es un techo físico (256 niveles + ruido acoplado del WiFi → ~6-7 bits efectivos) y el playback por loop Python no escala a >8 kHz. El MAX98357A da 16-bit con DMA por hardware (cero jitter) y permite 16 kHz. Creado `docs/genesis/decisions/ADR-001-speaker-i2s-max98357a.md` con cableado (pines libres 26/27/14 — corregido GPIO22 que choca con motor IN3), cambios de firmware (`machine.I2S`) y de laptop (`pcm_s16le` 16-bit @ 16 kHz), más mitigaciones ya aplicadas y un filtro RC opcional.
**Resultado:** Plan de migración listo y trazable (ADR-001). Pieza pendiente de compra; el trabajo de hoy exprime el DAC actual mientras tanto.
**Próximo paso:** Conseguir el MAX98357A (~20 BOB en Potosí), registrarlo con `genesis-cost`, branch `feat/speaker-i2s`, migrar firmware+laptop juntos.

## 2026-06-26
**Tipo:** build
**Componente:** voz/asistente (P9)
**Descripción:** Arrancado P9 (Asistente Personal), slice de **información inmediata**: hora, fecha y clima. Implementado por **detección de intención + inyección al system prompt** (no tool-calling nativo) → funciona con los 3 backends incluido el LLM local chico, y respeta el pipeline de TTS por frase. Nuevo módulo `robot_bob/assistant.py` (solo stdlib: `datetime` + `urllib`): detecta intención por keywords, formatea hora/fecha en español (nombres hardcodeados, locale Windows poco fiable) y consulta el clima vía **wttr.in** (sin API key, caché 10 min, timeout 3 s, fallback si no hay red). `voice_pipeline._stream_llm` concatena `contexto_asistente(texto)` al prompt solo cuando hay intención; el LLM redacta con la personalidad de Bob (no lee datos crudos). Config nueva: `CLIMA_ENABLED`, `CLIMA_CIUDAD="Potosí"`. Test `tests/test_9_asistente.py` (intención, formato con fecha fija, inyección, clima en vivo) — todo OK, clima real devolvió "Despejado 2°C".
**Resultado:** Bob ya responde hora/fecha/clima en charla. ROADMAP actualizado (P9 🚧 en progreso; P3 marcado 100% completo, foco viejo corregido).
**Próximo paso:** P9 slice 2 — **Productividad** (recordatorios/alarmas/temporizadores): scheduler local + disparo proactivo (Bob habla al vencer, reusando el camino de soliloquio/habla autónoma de la StateMachine).

## 2026-06-26
**Tipo:** build
**Componente:** voz/asistente (P9)
**Descripción:** Completado P9 slice 2 (**Productividad**): recordatorios, temporizadores y alarmas. Nuevo módulo `robot_bob/reminders.py` (stdlib): `parse_recordatorio(texto)` detecta gatillos (recuérdame/avísame/despiértame/alarma/temporizador) + tiempo **relativo** ("en N minutos/segundos/horas", acepta números escritos) o **absoluto** ("a las 3 de la tarde" → 15:00, si ya pasó → mañana), y extrae el "qué". `ReminderStore` thread-safe (agregar/vencidos/pendientes). En `voice_pipeline`: al crear, Bob confirma en charla ("¡Listo! Te aviso en 5 minutos."); un monitor nuevo (`iniciar_recordatorio_monitor` + `_recordatorio_loop`, arrancado en `main.py`) revisa cada segundo y al vencer **anuncia proactivamente** reusando `decir_soliloquio` (anti-eco + OLED + habla). Defiere si hay charla en curso (no saca de la cola hasta estar libre). Config: `RECORDATORIOS_ENABLED`. Test `test_9_asistente.py` extendido (parse relativo/absoluto, no-gatillo, store) — TODO OK.
**Resultado:** P9 funcional de punta a punta: info inmediata (hora/fecha/clima) + productividad (recordatorios proactivos). Recordatorios en memoria (no sobreviven reinicio — persistencia SQLite es futuro). ROADMAP P9 actualizado.
**Próximo paso:** Validar P9 en hardware completo (`main.py`) en la feria. Siguiente prioridad del roadmap: **P5 (Juegos)** — adivinanzas/trivia (LLM) + piedra-papel-tijera (MediaPipe Hands).

## 2026-06-29
**Tipo:** build
**Componente:** voz/asistente (P9)
**Descripción:** Cerrado el pendiente opcional de P9: **noticias**. Mismo patrón intent+inyección en `robot_bob/assistant.py` (solo stdlib, ahora suma `xml.etree`). `_quiere_noticias()` detecta por keywords (noticias/titulares/novedades/"qué pasa en el mundo"…); `obtener_noticias()` consulta el **RSS de Google News** (`es-419/BO`, sin API key, cero setup como wttr.in), parsea con `xml.etree`, recorta el sufijo " - Fuente" de cada título, cachea ~10 min, timeout 4 s, fallback si no hay red. `contexto_asistente()` inyecta N titulares (config `NOTICIAS_MAX=3`) al system prompt; el LLM los redacta con la personalidad de Bob. Config nueva: `NOTICIAS_ENABLED`, `NOTICIAS_RSS_URL`, `NOTICIAS_MAX` (en `config.py`, que es gitignored; `assistant.py` trae los mismos defaults en el `except` → funciona sin tocar config). Smoke test en vivo: intención OK, fetch trajo 3 titulares reales, contexto bien formado.
**Resultado:** P9 (Asistente Personal) **100% completo**: info inmediata (hora/fecha/clima) + noticias + productividad (recordatorios proactivos). ROADMAP actualizado (P9 sin pendiente opcional). Commit `1d3f5fa`.
**Próximo paso:** Siguiente prioridad del roadmap: **P5 (Juegos)** — adivinanzas/trivia (LLM) + piedra-papel-tijera (MediaPipe Hands).
