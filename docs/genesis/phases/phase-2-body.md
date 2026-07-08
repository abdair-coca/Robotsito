# Fase 2 — El cuerpo se mueve

> **Nota (julio 2026) — pivote de herramienta CAD:** se descontinúa el flujo de
> mega-prompts con freecad-mcp para el diseño de piezas. Los resultados no
> fueron ni cercanos a lo esperado para piezas con tolerancias críticas
> (encastres, horns de servo, bores de rodamiento). El modelado de piezas se
> hace **manualmente en Fusion 360** desde julio 2026. Ver
> [ADR-002](../decisions/ADR-002-pivote-fusion360-manual.md).

**Objetivo:** Bob camina — piernas físicas con control clásico, respaldadas por
un gemelo digital en simulación y primeros experimentos de aprendizaje.

- **Estado:** EN CURSO (en paralelo con el cierre de Fase 1)
- **Inicio:** Jul 2026 | **Fin estimado:** Jun 2027 (puede extenderse por el
  alcance ampliado; sin fecha nueva comprometida todavía)
- **Componentes base:** lo construido en Fase 1 + hardware de locomoción
  (8× servos MG996R, IMU, piezas impresas en 3D)

## Estructura: 4 pistas paralelas

La fase se trabaja en 4 pistas que avanzan en paralelo, no como bloque
secuencial único:

### 2A · Piernas físicas (control clásico) — pista principal
La que produce el hito visible de "Bob camina".

- [ ] **a) Modelado manual en Fusion 360** (julio 2026, dedicación completa):
      geometría de piernas (8 DOF, 4 por pierna), soportes de servo MG996R,
      bracket de cadera, ensamblaje de articulaciones. Sin código, sin
      automatización — pieza por pieza.
- [ ] b) Impresión 3D + ensamblaje
- [ ] c) Cinemática inversa + tablas de marcha
- [ ] d) Balance con IMU

### 2B · Gemelo digital en MuJoCo
Exportar el modelo de Fusion 360 a URDF; validar masas/inercias contra el
robot real. Corre en paralelo a 2A sin bloquearla.

- [ ] Export Fusion 360 → URDF
- [ ] Modelo cargando y estable en MuJoCo
- [ ] Masas/inercias validadas contra el robot físico

### 2C · Fundamentos de RL
Empezar simple — cartpole/péndulo invertido en Gymnasium o MuJoCo, **no** en
el bípedo completo. Solo si avanza bien, intentar una política simple de
balance en el gemelo digital.

- [ ] Cartpole/péndulo invertido resuelto y entendido
- [ ] (condicional) Política simple de balance en el gemelo digital

### 2D · Exploración de imitation learning
Menor prioridad, exploratoria. Captura de trayectorias por teleoperación/mano;
primeros experimentos con LeRobot en tareas simples.

- [ ] Captura de trayectorias por teleoperación
- [ ] Primer experimento con LeRobot documentado

## Hito de cierre de fase

La Fase 2 se considera cerrada cuando se cumplen **ambos**:

1. Bob camina unos pasos con control clásico (2A completa).
2. Hay un primer experimento **sim-to-real** documentado desde el gemelo
   digital (2B+2C), aunque sea imperfecto.

## Costo acumulado

Ver [budget.md](../budget.md).
