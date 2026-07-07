---
name: genesis-component
description: Documentar un componente técnico (hardware o software) del Proyecto Génesis. Usar cuando el usuario pida "documenta componente", "crea doc de", "documenta módulo", o quiera una ficha técnica de una pieza o módulo.
---

# Documentar un componente

Crea o actualiza un archivo `.md` de ficha técnica en `docs/genesis/`.

## Pasos

1. Pregunta el **tipo**: hardware o software.
2. Define la ruta destino (nombre en **kebab-case**):
   - hardware → `docs/genesis/hardware/<nombre>.md` (ej. `mpu6050-imu.md`)
   - software → `docs/genesis/software/<nombre>.md`
3. Si el archivo **ya existe**, actualízalo (no lo sobreescribas en blanco).
   Si no existe, créalo con esta plantilla:

```
# <Nombre del componente>

- **Tipo:** hardware | software
- **Estado:** active | experimental | deprecated
- **Fase:** Fase N

## Propósito
<para qué sirve>

## Conexiones / Dependencias
<pines, buses, librerías o módulos de los que depende>

## Notas técnicas
<detalles, gotchas, valores de calibración, enlaces>
```

## Reglas

- Solo documentación. **No genera código** ni toca archivos `.py`.
- Nombres de archivo siempre en kebab-case.
- Crea la carpeta `hardware/` o `software/` si no existe.
