---
name: bob-run
description: Arrancar el sistema completo del robot Bob (main.py) con orden correcto y cierre limpio. Usar cuando el usuario pida "arrancar el robot", "correr Bob", "iniciar main", "ejecutar el robot completo", o cualquier ejecución de producción.
---

# Arrancar Robot Bob (sistema completo)

Punto de entrada: `robot_bob/main.py`. Inicia los 6 componentes en orden y los cierra limpiamente con Q o Ctrl+C.

## Prerequisitos antes de arrancar

1. **Hardware encendido:**
   - ESP32 DevKit conectado por USB (COM3) — la luz roja debe estar encendida
   - ESP32-CAM con WiFi activo (LED parpadeando) en 192.168.0.22
2. **Tests pasados:** los 3 tests aislados deben funcionar antes de arrancar main.py (ver skill `bob-test`)
3. **Sin puertos ocupados:**
   - Arduino IDE, PuTTY, monitor serial cerrados
   - Ninguna ventana del navegador con `http://192.168.0.22:81/stream`
4. **Internet activo** para Groq API (LLM + STT)

## Comando

Desde `scripts/robot_bob/`:

```bash
python main.py
```

## Flujo de arranque esperado

```
═══════════════════════════════════
  Robot Bob — Sistema Integrado
═══════════════════════════════════
[serial] Conectado en COM3
Conectando al stream de la cámara...
[stream] Conectado.
[tracker] Cámara lista: 320x240
Esperando primer frame (máx 15 s)...
Robot Bob activo. Presiona Q para salir.
```

Si alguna línea no aparece en orden, hay un problema. Detener con Ctrl+C y diagnosticar (ver `bob-diagnose`).

## Cómo cerrar correctamente

- **Q** en la ventana de video (preferido — el `finally` del main loop ejecuta limpieza)
- **Ctrl+C** en terminal (también dispara el `finally`)
- **NO usar TaskKill ni cerrar la terminal con X** — deja COM3 huérfano y la próxima ejecución falla

## Comportamiento esperado durante uso

1. Sin cara: el robot hace movimientos aleatorios "aburridos" (waypoints), OLED en ESPERANDO
2. Apareces frente a cámara: transición a PRESENCE, OLED muestra SIGUIENDO, servos te siguen
3. Tras ~3s de permanencia: con 35% probabilidad inicia conversación automática, sino solo te mira
4. Dices "Bob" en cualquier momento: arranca conversación inmediata
5. Tras conversación: vuelta a IDLE con cooldown de 30s antes de poder iniciar otra automática

## HUD en pantalla

- Estado actual (IDLE, PRESENCE, LISTENING, etc.) en color
- Pan/Tilt actuales
- FPS del main loop (debe ser ~20)
- Círculo verde/rojo arriba-derecha = stream OK/error

## Si falla al arrancar

| Error | Causa probable | Acción |
|-------|----------------|--------|
| `SerialException: port already in use` | Otro programa con COM3 | Cerrar Arduino IDE / monitor serial |
| `No llegan frames de la cámara` | ESP32-CAM no encendida o IP errónea | Verificar LED + ping 192.168.0.22 |
| `Groq API error` durante warmup | API key inválida o sin internet | Revisar `voicechatLap/config.py` y conexión |
| Cuelgue al iniciar | Stream del ESP32-CAM saturado | Reiniciar ESP32-CAM físicamente |

## Después de cerrar

- El robot vuelve a posición home (90, 90) automáticamente
- OLED queda en ESPERANDO
- COM3 se libera (próxima ejecución funcionará)
