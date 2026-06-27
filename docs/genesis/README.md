# Proyecto Génesis

Construir un **humanoide de bajo costo con IA integrada**, de forma progresiva,
durante 2.5 años (Jun 2026 – Nov 2028). Génesis es la evolución de **RobotCreeper / Bob**:
todo lo construido en RobotCreeper es la **base de la Fase 1**.

## Objetivo central

Demostrar que un humanoide funcional puede construirse con **presupuesto mínimo**.
Cada componente se registra con su costo (USD y BOB) para que el presupuesto total
sea transparente y reproducible.

Creador: Abdair — estudiante de Ingeniería Informática, UATF, Bolivia.

## Las 4 fases

| Fase | Nombre | Periodo | Objetivo |
|------|--------|---------|----------|
| 1 | El alma de Bob | Jun–Dic 2026 | Memoria persistente, razonamiento, personalidad, reconocimiento de personas |
| 2 | El cuerpo se mueve | Ene–Jun 2027 | Equilibrio bipedal básico con IMU, marcha, RL simple |
| 3 | Mente y cuerpo unidos | Jul–Dic 2027 | Embodied AI loop: visión + decisión + acción, tareas autónomas |
| 4 | El mundo lo conoce | Ene–Nov 2028 | Open source completo, ferias, paper publicado |

## Cómo navegar la documentación

- **`phases/`** — una ficha por fase: objetivo, estado, hitos, costo acumulado.
- **`log/`** — bitácora de progreso por fase. Una entrada por avance (fecha ISO).
- **`budget.md`** — presupuesto vivo: tabla de componentes con costo y estado.
- **`decisions/`** — decisiones técnicas (ADR-NNN-nombre.md).
- **`experiments/`** — experimentos (EXP-F-NNN-nombre.md, F = número de fase).
- **`hardware/`** / **`software/`** — fichas técnicas de componentes (kebab-case).

## Estado actual

**FASE 1 — El alma de Bob** (EN PROGRESO).
Bob ya cuenta con: face tracking (OpenCV/MediaPipe), pipeline STT→Groq→TTS,
ojos OLED animados (SH1106), servo pan/tilt, cámara MJPEG, memoria SQLite,
máquina de estados y motor de comportamiento.
