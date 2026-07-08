# ADR-002 — Pivote de freecad-mcp a modelado manual en Fusion 360

- **Fecha:** 2026-07-07
- **Estado:** Aceptado
- **Fase:** 2 — El cuerpo se mueve
- **Autor:** Abdair + Claude

---

## Contexto

El plan original para las piezas de Génesis era generar el CAD por
automatización: mega-prompts contra **freecad-mcp** (FreeCAD manejado por LLM
vía MCP). Se probó durante junio 2026 para piezas de las piernas.

Las piezas de locomoción tienen **tolerancias críticas** que deciden si el
robot se sostiene o no: encastres pieza-pieza, horns de servo MG996R, bores
de rodamiento. El flujo por prompts no logró resultados ni cercanos a lo
esperado en esa precisión: la iteración prompt→pieza→corrección resultó más
lenta y menos confiable que modelar a mano.

## Decisión

Desde **julio 2026**, el modelado de piezas de Génesis se hace **manualmente
en Fusion 360**, pieza por pieza. Se descontinúa el flujo de mega-prompts con
freecad-mcp para diseño de piezas.

Julio 2026 queda dedicado íntegramente al modelado de las piernas (pista 2A-a
de la Fase 2): geometría 8 DOF (4 por pierna), soportes de servo MG996R,
bracket de cadera y ensamblaje de articulaciones. Sin código, sin
automatización.

## Consecuencias

- **(+)** Control total de tolerancias; lo que decide el éxito mecánico queda
  en manos humanas.
- **(+)** Fusion 360 exporta a URDF (vía plugins) → habilita la pista 2B
  (gemelo digital MuJoCo).
- **(−)** Más lento que la promesa de la automatización; julio 2026 completo
  se dedica solo a modelado.
- **(−)** Curva de aprendizaje de Fusion 360.
- El rango de fechas de la Fase 2 (antes Ene–Jun 2027) puede necesitar
  extenderse; se anota la incertidumbre sin comprometer fecha nueva.
- freecad-mcp queda instalado para consultas puntuales (medir, inspeccionar),
  no para generar piezas.
