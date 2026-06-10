---
name: bob-add-state
description: Agregar un nuevo estado a la máquina de estados del robot Bob (StateMachine + OLED + transiciones). Usar cuando el usuario pida "agregar un estado nuevo", "que Bob tenga modo X", "añadir comportamiento Y", o cualquier extensión que requiera nuevos estados del robot.
---

# Agregar un Estado Nuevo a Robot Bob

Cada estado del robot afecta 4-5 componentes. Olvidar uno deja inconsistencias visuales o de control.

## Checklist obligatorio

Para agregar un estado nuevo, modificar **en este orden**:

### 1. Enum `RobotState` en `state_machine.py`

```python
class RobotState(enum.Enum):
    IDLE              = 'IDLE'
    PRESENCE          = 'PRESENCE'
    # ... existentes
    MI_NUEVO_ESTADO   = 'MI_NUEVO_ESTADO'   # ← agregar
```

### 2. Mapeo a comando OLED en `_OLED_STATE`

```python
_OLED_STATE: dict[RobotState, str] = {
    # ... existentes
    RobotState.MI_NUEVO_ESTADO: 'ESCUCHANDO',  # ← qué comando OLED enviar
}
```

**Importante:** el firmware del ESP32 (`Esp32/main.py`) acepta estos comandos:
`ESPERANDO`, `ESCUCHANDO`, `PENSANDO`, `HABLANDO`, `FELIZ`, `CURIOSO`, `SIGUIENDO`.

Si necesitas una expresión nueva, hay que **agregarla al firmware del ESP32 también** (modificar `Esp32/oled_ojos.py` con un nuevo entry en `_STATES`).

### 3. Métodos de transición en `StateMachine`

Si el estado debe ser invocable desde fuera (otro hilo), agregar método:

```python
def iniciar_mi_nuevo_estado(self) -> None:
    self._transicionar(RobotState.MI_NUEVO_ESTADO)
```

Y un `threading.Event` si otro componente debe esperar a este estado:

```python
def __init__(self, ...):
    ...
    self.ev_mi_nuevo = threading.Event()
```

Y limpiarlo en `_transicionar_locked`:

```python
self.ev_mi_nuevo.clear()
if nuevo == RobotState.MI_NUEVO_ESTADO:
    self.ev_mi_nuevo.set()
```

### 4. Comportamiento de servo en `behavior.py`

Definir cómo se mueven los servos en ese estado:

```python
self.alpha = {
    # ... existentes
    RobotState.MI_NUEVO_ESTADO: 0.10,   # suavizado
}
self.max_paso = {
    # ... existentes
    RobotState.MI_NUEVO_ESTADO: 1.5,    # velocidad max
}
```

Y agregar branch en `_tick`:

```python
elif estado == RobotState.MI_NUEVO_ESTADO:
    self._tick_mi_nuevo(det, ahora)
```

Crear el método `_tick_mi_nuevo(self, det, ahora)` que defina la lógica de movimiento.

### 5. HUD en `main.py`

Agregar color para el estado en el HUD:

```python
COLOR_STATE = {
    # ... existentes
    'MI_NUEVO_ESTADO': (200, 100, 255),
}
```

### 6. Transiciones desde otros estados

Decidir desde qué estados se puede entrar y salir. Modificar `notificar_cara`, `notificar_wake_word`, `tick_conversation_idle`, o agregar nuevos métodos.

Ejemplo: si el estado debe entrarse cuando se detecta una sonrisa:
- Detectar sonrisa en `facial_tracker.py` (requiere landmarks de MediaPipe Face Mesh, no BlazeFace)
- Llamar `sm.iniciar_mi_nuevo_estado()` desde el main loop

## Anti-patrones a evitar

1. **No emitir comandos OLED directamente** desde fuera del state machine. Siempre vía `_OLED_STATE`.
2. **No olvidar limpiar el event** correspondiente al transicionar a otro estado.
3. **No agregar un estado sin pensar en cómo se sale de él** — sin transición de salida, el robot se queda atascado.
4. **No agregar expresiones OLED sin modificar el firmware del ESP32** — el comando se enviará pero la cara no cambiará.

## Verificación

Después de agregar el estado:
1. Forzar manualmente la transición con `sm.iniciar_mi_nuevo_estado()` en un test ad-hoc
2. Confirmar que el OLED cambia
3. Confirmar que los servos se comportan como esperabas en ese estado
4. Confirmar que las transiciones de SALIDA funcionan (no se queda atascado)

## Estados ya existentes para inspirarse

| Estado | OLED | Trigger | Salida |
|--------|------|---------|--------|
| IDLE | ESPERANDO | inicio / fin de conversación | cara detectada |
| PRESENCE | SIGUIENDO | cara detectada | sin cara >1.5s o trigger conv. |
| LISTENING | ESCUCHANDO | wake word / probabilidad / explícito | audio capturado |
| THINKING | PENSANDO | audio capturado | primera frase del LLM lista |
| SPEAKING | HABLANDO / FELIZ | TTS empieza | TTS termina o barge-in |
| CONVERSATION_IDLE | ESPERANDO | fin de turno | nuevo turno o timeout 6s |
