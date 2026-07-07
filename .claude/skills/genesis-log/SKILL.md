---
name: genesis-log
description: Registrar una entrada de progreso en la bitácora de la fase activa del Proyecto Génesis. Usar cuando el usuario pida "registra en el log", "log de hoy", "anota esto", o quiera dejar constancia de un avance.
---

# Registrar entrada en el log de Génesis

Añade una entrada nueva al **final** del log de la fase activa. **No reescribe** el archivo.

## Pasos

1. Lee la **fase activa** en `CLAUDE.md` (sección "Fase activa", ej. FASE 1 → N=1).
2. Archivo destino: `docs/genesis/log/phase-[N]-log.md`.
3. Pide al usuario (si no lo dio ya):
   - **Tipo:** build | fix | experiment | milestone | decision
   - **Componente:** módulo o área (ej. memoria, voz, proyecto/genesis)
   - **Descripción:** qué se hizo
   - **Resultado:** qué cambió / qué se logró
   - **Próximo paso:** siguiente acción
4. Usa la **fecha de hoy en ISO** (`YYYY-MM-DD`) como encabezado.
5. **Append** al final del archivo con este formato exacto:

```
## YYYY-MM-DD
**Tipo:** <tipo>
**Componente:** <componente>
**Descripción:** <descripción>
**Resultado:** <resultado>
**Próximo paso:** <próximo paso>
```

## Reglas

- Solo añadir al final. Nunca borrar ni reordenar entradas previas.
- Si ya existe una entrada con la fecha de hoy, añade igual una nueva debajo (no fusiones).
- No genera código. Solo documentación.
