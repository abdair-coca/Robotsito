# Robot Bob — Índice de contexto para agentes LLM

Punto de entrada para que cualquier LLM trabaje en este proyecto sin redescubrir
todo. Lee primero esto, luego el doc específico que necesites.

## Qué es

**Bob** es un robot interactivo de feria (reconocimiento facial + seguimiento
pan/tilt + ojos OLED + conversación por voz con IA). Cerebro en la **laptop**
(MediaPipe + Groq); los ESP32 solo hacen actuadores y video. Detalle en
[README.md](README.md).

## Regla #0: Uso obligatorio de Graphify para Lectura (Ahorro de Tokens)

**SIEMPRE que se pida leer, comprender o analizar la arquitectura/código del proyecto (incluso si el usuario NO menciona "graphify" explícitamente), el LLM DEBE usar Graphify como primer paso:**
1. Consultar `graphify-out/GRAPH_REPORT.md` o ejecutar `graphify query "<pregunta>"` / `graphify explain "<concepto>"`.
2. Usar `graphify path "<A>" "<B>"` para entender relaciones entre módulos.
3. NO leer múltiples archivos fuente completos ni recorrer carpetas a ciegas si Graphify ya abstrae la estructura y las relaciones.
4. Tras modificar código, ejecutar `graphify update .` para mantener el grafo al día.

## Regla #1: el sistema canónico es `robot_bob/`

Todo el desarrollo activo ocurre en `robot_bob/` (cerebro Python). El resto del
repo es firmware (`firmware/`), dependencias vivas (`shared/`) o base histórica
(`legacy/`, **no editar** salvo pedido explícito).

## Dónde está cada cosa

| Necesito entender… | Lee |
|---|---|
| Hilos, FSM, módulos, emociones, backends | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Pines, IPs, puertos, firmware, brownout | [docs/HARDWARE.md](docs/HARDWARE.md) |
| Discovery de IPs, mDNS, multi-WiFi, troubleshooting de red | [docs/NETWORK.md](docs/NETWORK.md) |
| Qué falta por hacer y en qué orden | [docs/ROADMAP.md](docs/ROADMAP.md) |
| Feature "Actitud" (soliloquio/muecas) | [docs/FEATURES.md](docs/FEATURES.md) |
| Motores DC / locomoción | [docs/LOCOMOTION.md](docs/LOCOMOTION.md) |
| Historia / etapas de construcción | [docs/HISTORY.md](docs/HISTORY.md) |

## Convenciones (obligatorias)

- **Idioma:** código, comentarios y docs en **español**.
- **Config centralizada:** todo lo ajustable va en `robot_bob/config.py` (gitignored),
  nunca hardcodear en módulos. Los secretos van en `robot_bob/.env`.
- **Canal de control:** `SerialManager` es el único dueño de COM3/TCP 5007. Nunca
  escribir al serial directo.
- **Cable = fallback de oro:** la arquitectura por USB COM3 nunca se elimina.
- **Knowledge graph:** usa `graphify query/path/explain` para preguntas de arquitectura;
  corre `graphify update .` tras modificar código. Reglas en [CLAUDE.md](CLAUDE.md).
- **Skills de Claude Code:** `bob-run`, `bob-test`, `bob-diagnose`, `bob-add-state`.

