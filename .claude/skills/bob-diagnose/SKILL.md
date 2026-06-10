---
name: bob-diagnose
description: Diagnóstico sistemático de problemas del robot Bob — stream que se cuelga, COM3 ocupado, voz que no responde, servos bruscos, OLED no actualiza. Usar cuando el usuario reporte cualquier malfuncionamiento durante un test o ejecución, antes de proponer cambios al código.
---

# Diagnóstico de Problemas — Robot Bob

Antes de modificar código, identifica QUÉ componente falla. Sigue el árbol correspondiente.

## Síntoma: "El stream se cuelga / hay reconexiones"

```
[stream] Reconectando (timed out)...
```

### Diagnóstico

1. **Correr `test_2b_stream_only.py` aislado**
   - Si **no se cuelga** → problema es la carga del main loop (CPU/GIL). Ver fix abajo.
   - Si **sí se cuelga** → problema del ESP32-CAM. Ver workarounds del ESP32-CAM.

2. **Verificar la red:** otro dispositivo en la red puede saturar el WiFi del ESP32-CAM. Probar el stream en navegador: `http://192.168.0.22:81/stream` y ver si va estable.

### Fix si es saturación de CPU

Causa raíz: el ESP32-CAM emite a 60-80 FPS y nuestro main loop intenta procesar cada frame. El hilo `stream-reader` se queda sin GIL para hacer `sock.recv()`.

Cambios ya aplicados (verificar que están en su lugar):
- `cv2.setNumThreads(1)` al inicio de main.py / test_2_tracker.py
- `TARGET_FPS = 20` con `time.sleep(TARGET_PERIOD - elapsed)` al final del main loop
- `STREAM_TIMEOUT_S = 5.0` en `facial_tracker.py`

Si todavía falla: bajar `TARGET_FPS` a 15.

### Workarounds del ESP32-CAM (firmware-side)

Solo si test 2b también se cuelga:
- Bajar resolución a QQVGA (160x120) en el firmware
- Bajar XCLK frequency a 10 MHz
- Bajar FPS configurado de 25 a 10

## Síntoma: "COM3 no abre"

```
SerialException: port already in use
[serial] No conecta en COM3: ...
```

### Diagnóstico

1. ¿Hay otro programa con COM3? Arduino IDE, PuTTY, Thonny, monitor serial de VS Code, otro proceso Python.
2. ¿La ejecución anterior cerró bien? Si se mató con TaskKill, COM3 queda huérfano. Solución: desconectar el USB del ESP32 y reconectar.
3. ¿El cable USB es de datos, no solo de carga? Algunos cables USB-C antiguos solo dan corriente.

### Verificar puerto correcto

En PowerShell:
```powershell
Get-WmiObject Win32_SerialPort | Select-Object Name, DeviceID
```

Si el ESP32 aparece como `COM4` o `COM5`, cambiar `PUERTO_SERIAL` en `main.py` y `tests/*.py`.

## Síntoma: "No detecta el wake word 'Bob'"

### Diagnóstico

1. **Verificar mic:** correr un test rápido con `sounddevice`:
   ```python
   import sounddevice as sd; print(sd.query_devices())
   ```
   El dispositivo por defecto debe ser el mic correcto.

2. **Hablar más fuerte y cerca del mic.** El wake monitor graba en chunks de 3s y transcribe con Whisper — si tu voz queda por debajo del piso de ruido, Whisper devuelve string vacío.

3. **Ver qué transcribe Whisper:** agregar `print` temporal en `voice_pipeline.py` antes de `self._wake.detect(texto)`:
   ```python
   print(f'[wake-debug] transcrito: "{texto}"')
   ```

4. **Bajar el umbral fuzzy:** en `voicechatLap/config.py` cambiar `WAKE_FUZZY_THR = 78` a `65` (más tolerante).

## Síntoma: "Servos se mueven bruscos / robóticos"

### Diagnóstico

Causa probable: los parámetros del servo están calibrados para una frecuencia distinta del main loop.

Verificar en `facial_tracker.py`:
- `GANANCIA_PAN/TILT = 0.025` (era 0.018 a 60 Hz)
- `ADELANTO_MAX = 12` (era 6)

Verificar en `test_2_tracker.py` o `main.py`:
- `SERVO_SUAV = 0.78` (más reactivo)
- `SERVO_PASO = 3.0` (60°/seg a 20 Hz)

Si todo está correcto y sigue brusco: activar el `BehaviorEngine` que agrega easing y microsacadas.

## Síntoma: "OLED no cambia / queda congelado"

### Diagnóstico

1. **¿Llega el comando?** El throttle del SerialManager descarta `ESTADO:X` si es el mismo enviado hace <1s. Verificar que estás cambiando a un estado distinto.

2. **¿El firmware del ESP32 maneja ese estado?** Ver `Esp32/main.py` — la lista de estados soportados es: ESPERANDO, ESCUCHANDO, PENSANDO, HABLANDO, FELIZ, CURIOSO, SIGUIENDO.

3. **¿El FPS del OLED cayó durante audio playback?** Es esperado: el firmware reduce el hilo OLED a 4.5 Hz durante `reproduciendo=True` para priorizar el DAC. Verificable visualmente: durante TTS la boca se mueve más despacio.

## Síntoma: "El robot habla pero no responde a mi siguiente pregunta"

### Diagnóstico

Era un bug del refactor inicial. Ya está arreglado: `_run_conversation` mantiene un loop interno entre turnos sin requerir wake word.

Si reaparece: verificar que `voice_pipeline.py` tiene el método `_run_conversation` (no `_run_one_turn`).

## Síntoma: "Bob detecta wake word pero ignora la pregunta"

Limitación conocida cuando dices "Bob ¿pregunta?" todo junto rápido. El wake monitor captura el audio entero, extrae "Bob" y descarta el resto. El VoicePipeline graba después y captura silencio.

Workaround para el usuario: decir "Bob" → pausa breve → pregunta.

Fix futuro (no implementado): pasar el `ww.payload` del wake_monitor al `_run_conversation` como audio inicial.

## Cuando nada funciona

1. Cerrar todo (Ctrl+C)
2. Desconectar USB del ESP32 DevKit, reconectar
3. Reiniciar ESP32-CAM (cortar power)
4. Verificar `git status` — ¿hay cambios accidentales en código que funcionaba?
5. Correr test 1 (más simple) — si falla, el problema es de hardware/permisos
