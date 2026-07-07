---
name: genesis-cost
description: Registrar un componente de hardware con su costo en el presupuesto del Proyecto Génesis. Usar cuando el usuario pida "registra componente", "añade al presupuesto", "cuánto cuesta", o compre/consiga una pieza nueva.
---

# Registrar componente en el presupuesto

Añade una fila a la tabla de `docs/genesis/budget.md` y actualiza el total de la fase.

## Pasos

1. Pide al usuario (si no lo dio ya):
   - **Nombre** del componente
   - **Categoría:** compute | sensor | actuator | display | power | structure | other
   - **Precio USD**
   - **Dónde se consiguió**
   - **Fase:** Fase 1 | Fase 2 | Fase 3 | Fase 4
   - **Estado:** Integrado | Parcial | Comprado | Pendiente
2. Calcula **BOB automático**: `BOB = USD × 6.9` (redondea 2 decimales).
3. **Append** una fila a la tabla "Componentes":

```
| <nombre> | <categoría> | <USD> | <BOB> | <fase> | <estado> | <dónde> |
```

4. Actualiza la tabla "Totales por fase": suma el USD y BOB a la fila de la fase
   correspondiente y recalcula la fila **Total proyecto**.

## Reglas

- Tasa fija: **1 USD = 6.9 BOB**.
- No borra filas existentes. Solo añade y actualiza totales.
- No genera código. Solo documentación.
