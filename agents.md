# Creeper Robot Voice Chat - Context & Session Summary

Este archivo proporciona el contexto completo del proyecto y detalla la arquitectura, configuración y cambios implementados durante la sesión actual para guiar a futuros agentes en el desarrollo de Creeper.

---

## 1. Perspectiva General del Proyecto

**Creeper** es un robot interactivo basado en un chip **ESP32** que se comunica vía TCP y Serial con una laptop (cliente de Python) para entablar conversaciones inteligentes de voz.

### Conexiones de Red y Hardware
- **ESP32 IP**: `192.168.0.23` (por defecto)
- **Port Mic (5005)**: Puerto TCP donde el ESP32 transmite audio en flujo continuo (micrófono del robot).
- **Port Spk (5006)**: Puerto TCP donde el cliente envía audio estructurado para reproducir (altavoz del robot) y comandos especiales.
- **Serial (COM3)**: Conexión serial para enviar comandos de estados emocionales (`ESTADO:XX`) para actualizar la pantalla OLED del robot.

---

## 2. Estructura de Directorios y Módulos

El proyecto está separado en dos arquitecturas independientes de chat:

1. **`VoiceChat/`**: 
   - Utiliza exclusivamente el hardware del robot (micrófono del robot y parlante del robot).
   - Se mantiene intacto para conservar el comportamiento puro del hardware integrado.
2. **`voicechatLap/`** (Directorio modificado en esta sesión):
   - Híbrido que permite usar el micrófono de la laptop para mayor fidelidad de captura, y cuenta con selectores configurables para rutear el micrófono y altavoz al robot o a la laptop de forma dinámica.

---

## 3. Logros e Implementaciones de esta Sesión

En esta sesión, añadimos dos interruptores de configuración críticos en `voicechatLap/config.py` y adaptamos la lógica en `voicechatLap/chat.py` para soportarlos dinámicamente sin interrumpir la conectividad base del ESP32.

### A. Parámetros de Configuración en `voicechatLap/config.py`
- `USE_ROBOT_SPEAKER` (True / False):
  - **`True`**: El audio sintetizado (TTS) por Groq/Edge-TTS se envía al altavoz del robot en formato crudo `u8 @ 8 kHz`.
  - **`False`**: El audio se reproduce localmente en los parlantes de la laptop en alta fidelidad (`MP3` reproducido vía `pygame.mixer`).
- `USE_ROBOT_MIC` (True / False):
  - **`True`**: La voz del usuario se graba utilizando el micrófono del ESP32 (flujo continuo de 8 kHz u8 filtrado con detector de silencios).
  - **`False`**: La voz se graba utilizando el micrófono integrado de la laptop (`16 kHz WAV` con `sounddevice`).

### B. Transcripción y Ruteo Dinámico de Audio (`voicechatLap/chat.py`)
- **Grabación**: `record_utterance` cambia dinámicamente su entrada (del búfer del socket del robot si `USE_ROBOT_MIC` es `True`, o de `sounddevice` si es `False`).
- **Procesamiento de Voz**: Si se graba desde el robot, el audio `uint8` se preprocesa y se limpia de offset DC con `uint8_frames_to_wav_bytes` antes de enviarlo a Groq Whisper. Si se graba desde la laptop, el flujo PCM se empaqueta directamente en WAV.
- **Sintetizador**: Si el altavoz de la laptop está activo, se evita la degradación de filtros FFMPEG (Nyquist 4kHz y reducción a 8 bits u8) y se extraen los bytes MP3 de alta definición directamente.

### C. Barge-in (Interrupción por Voz) Inteligente
El usuario solicitó que **barge-in sólo funcione al utilizar el micrófono de la laptop** (`USE_ROBOT_MIC = False`).
- Se implementó la subrutina `_barge_in_listener` en la clase `Chat`.
- Monitorea el flujo de entrada de la laptop en paralelo con la síntesis de audio de Groq.
- Al detectar habla legítima (VAD positivo y RMS superior al ruido de fondo de la reproducción):
  1. Detiene la salida de audio activa llamando a la función correspondiente:
     - Si es en el robot, envía la señal TCP de stop (`0xFFFFFFFF`).
     - Si es en la laptop, apaga el `pygame.mixer`.
  2. Cancela el flujo de generación del LLM en tiempo real.
  3. Continúa grabando el resto del habla del usuario para inyectarlo como el siguiente mensaje en la conversación.

---

## 4. Guía para Futuros Desarrolladores / Agentes

### Prerrequisitos de Librerías
Asegúrate de que el entorno virtual tenga las siguientes dependencias instaladas:
```bash
pip install sounddevice webrtcvad pygame numpy scipy groq edge-tts imageio-ffmpeg rich
```

### Ejecutar y Testear
Para iniciar la sesión conversacional híbrida:
```bash
python voicechatLap/main.py
```
> **Nota de Dependencia del ESP32**: Incluso si configuras `USE_ROBOT_MIC = False` y `USE_ROBOT_SPEAKER = False`, la laptop **debe** establecer conexión sockets TCP con el IP del ESP32. De lo contrario, el firmware del ESP32 permanecerá bloqueado esperando que ambos sockets se acepten y no procesará su bucle principal.

### Ajuste de Sensibilidad de Barge-in
Si hay falsos positivos (eco del parlante de la laptop interrumpiendo el chat):
- Incrementa `BARGE_IN_RMS` (línea 524 de [voicechatLap/chat.py](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/voicechatLap/chat.py)) a un valor mayor, ej: `0.035`.
- Si el barge-in es muy lento para activarse, reduce `BARGE_IN_SUSTAINED_MS` en [voicechatLap/config.py](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/voicechatLap/config.py).
