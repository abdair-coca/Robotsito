## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Higiene de tokens

El costo dominante es el input re-enviado cada turno. Para minimizarlo:
- Preferir `graphify query`/`explain` y lecturas ACOTADAS (`offset`/`limit`) sobre
  leer archivos enteros. No releer un archivo recién editado para "verificar":
  Edit/Write fallan si el cambio no aplicó.
- En Grep: usar `head_limit`, `glob`/`type` y `path` específicos. Nunca greps
  amplios que peguen en `legacy/`, venvs o `.claude/worktrees/` (ya excluidos en
  `.ignore`, pero igual acotar).
- Agrupar tool calls independientes en un mismo mensaje (paralelo).
- Mantener caveman activo (recorta output).

## Proyecto Génesis

Bob es el prototipo del Proyecto Génesis: humanoide de bajo costo con IA integrada.
Objetivo: demostrar que un humanoide funcional puede construirse con presupuesto mínimo.
Creador: Abdair (estudiante de Ingeniería Informática, UATF, Bolivia)
Inicio: Jun 2026 | Graduación estimada: Nov 2028

### Fase activa
FASE 1 — El alma de Bob (Jun 2026 - Dic 2026)
Objetivo: Bob tiene memoria, personalidad y reconoce personas.

### Current focus
[Abdair actualiza esto manualmente cada semana]
Semana del 25 Jun 2026: Iniciar sistema de documentación Génesis. Bob ya tiene: face tracking, voz (STT→Groq→TTS), ojos OLED, servo pan/tilt, cámara MJPEG.

### Estructura del proyecto
- robot_bob/       → código principal (behavior, memory, voice, face tracking, state machine)
- firmware/        → MicroPython ESP32 + ESP32CAM + OLED
- legacy/          → experimentos anteriores archivados
- docs/            → documentación técnica por módulo
- docs/genesis/    → documentación del Proyecto Génesis (fases, costos, log)
- .claude/skills/  → skills de Claude Code para este proyecto

### Skills disponibles
- bob-run          → arranca Bob completo
- bob-test         → prueba subsistemas individuales
- bob-diagnose     → diagnóstico de conexiones y estado
- bob-add-state    → añade estados a la máquina de estados
- genesis-log      → registra entrada de progreso en el log de fase activa
- genesis-cost     → registra un componente de hardware con su costo
- genesis-component → documenta un componente técnico (hw o sw)

### Convenciones
- Archivos de componentes: kebab-case (mpu6050-imu.md)
- Entradas de log: encabezado con fecha ISO (## 2026-06-25)
- Decisiones técnicas: docs/genesis/decisions/ADR-001-nombre.md
- Experimentos: docs/genesis/experiments/EXP-1-001-nombre.md (número de fase primero)
- Costos en USD y BOB (1 USD ≈ 6.9 BOB)
