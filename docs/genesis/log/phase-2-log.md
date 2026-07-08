# Bitácora — Fase 2: El cuerpo se mueve

Entradas en orden cronológico. Una entrada por avance. Encabezado con fecha ISO.
Registrar con la skill `genesis-log`.

## 2026-07-07
**Tipo:** decision
**Componente:** cad/herramientas
**Descripción:** Pivote de freecad-mcp (mega-prompts automatizados) a modelado
manual en Fusion 360 para las piezas de Génesis. Se probó el flujo automatizado
durante junio para piezas de las piernas; las tolerancias críticas (encastres,
horns de servo MG996R, bores de rodamiento) requieren una precisión que el
flujo por prompts no alcanzó. Ver [ADR-002](../decisions/ADR-002-pivote-fusion360-manual.md).
La Fase 2 se reestructura en 4 pistas paralelas (2A piernas físicas, 2B gemelo
digital MuJoCo, 2C fundamentos RL, 2D imitation learning) y arranca EN CURSO
desde julio 2026, en paralelo con el cierre de Fase 1.
**Resultado:** freecad-mcp no dio resultados cercanos a lo esperado para piezas
con tolerancias críticas. Queda descontinuado para diseño de piezas.
**Próximo paso:** Julio 2026 dedicado íntegramente a modelar las piernas
(8 DOF, 4 por pierna) en Fusion 360 manualmente: soportes MG996R, bracket de
cadera y ensamblaje de articulaciones.
