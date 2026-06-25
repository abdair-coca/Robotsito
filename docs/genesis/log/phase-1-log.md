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
