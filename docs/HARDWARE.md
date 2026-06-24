# Hardware — Robot Bob

Las 3 piezas físicas y cómo se conectan. Para cómo la laptop las encuentra en la
red ver [NETWORK.md](NETWORK.md); para los motores de tracción ver
[LOCOMOTION.md](LOCOMOTION.md).

---

## 1. Componentes

| Componente | Transporte | Función |
|---|---|---|
| **ESP32 DevKit** | USB **COM3** + WiFi (TCP) | OLED SH1106 (I2C **32/33**), servos pan/tilt (GPIO13/12), mic MAX9814 (GPIO34), speaker PAM8403 (GPIO25), motores DC L298N IN1-4 (GPIO19/21/22/23) + ENA/ENB PWM (GPIO16/4) |
| **ESP32-CAM** (AI-Thinker) | WiFi | Stream MJPEG en `:81/stream`, panel HTTP en `:80` |
| **Laptop** | local | Toda la lógica Python (visión, IA, audio) |

> IPs: **ya no se hardcodean** — `discovery.py` las resuelve sola (DHCP cambia). Ver
> [NETWORK.md](NETWORK.md). MACs: CAM `F8:B3:B7:A7:14:60`, DevKit `EC:E3:34:22:A6:6C`.

---

## 2. Puertos TCP

- `:81/stream` — ESP32-CAM: video MJPEG (`:80` = panel de control).
- `5005` — ESP32 → laptop: stream continuo del micrófono (solo si `USE_ROBOT_MIC`).
- `5006` — laptop → ESP32: audio TTS + comandos STOP/KEEPALIVE (solo si `USE_ROBOT_SPEAKER`).
- `5007` — laptop → ESP32: servidor de control de servos + OLED por WiFi (`USE_WIFI_SERIAL`).

El DevKit sirve control **y** audio en la **misma IP**. La laptop solo escribe al
puerto de control; el firmware no responde por TCP a propósito.

---

## 3. Firmware (corre DENTRO de los ESP32)

### DevKit — `firmware/Esp32/` (MicroPython, subir con Thonny/ampy)
| Archivo | Rol |
|---|---|
| `main.py` | Firmware completo: WiFi (multi-red, ver [NETWORK.md](NETWORK.md)), 2 hilos (`hilo_audio` half-duplex 8 kHz, `hilo_oled` ~10 fps) + loop de control. Bindea `5005`/`5006` (audio) y `5007` (control). Parser de texto compartido WiFi+USB (`aplicar_cmd`). |
| `oled_ojos.py` / `.mpy` | Motor de ojos emocionales (21 estados). El `.mpy` es la versión compilada que se sube; recompilar tras editar el `.py`. |
| `sh1106.py` | Driver del OLED SH1106. |
| `config.py` | **No está en el repo** (gitignoreado, vive en el dispositivo). Define `SSID`/`PASSWORD` (o `REDES`) y, opcionalmente, `STATIC_IP`/`GATEWAY`/`SUBNET`/`DNS`. |

Protocolo de texto (idéntico en USB y TCP 5007):
`H:<pan>,V:<tilt>\n` · `ESTADO:<NOMBRE>\n` · `SIGUIENDO:<dx>,<dy>\n` ·
`M:<izq>,<der>\n` (motores, [-100,100]: signo=dirección, magnitud=PWM).

### CAM — `firmware/Esp32ThinkerAICam/`
- Corre **CameraWebServer (Arduino/C)**, re-flasheable (ver sketch en
  `Documents/Arduino/CameraWebServerBob/`). Aporta el modelo
  `blaze_face_short_range.tflite` que carga el tracker de la laptop.
- `main.py` (MicroPython) de esa carpeta es un experimento viejo, **NO** es lo que
  corre. No editarlo.

---

## 4. ⚠️ Brownout — el cuello de botella físico

Síntoma: tras unos segundos de seguimiento, el **OLED parpadea/se reinicia** y los
**servos dejan de responder** aunque lleguen las coords. Causa: pico de corriente de
**servos + radio WiFi** sobre una fuente débil (cargador USB de pared) → caída de
voltaje → brownout/reset del ESP32.

- **Software (hecho):** `serial_manager.py` detecta el socket muerto y reconecta solo
  (cada 2 s). Recupera el control tras el reset, pero no lo evita.
- **Hardware (pendiente, lo resuelve el usuario):** fuente de **≥2 A**; idealmente un
  **rail separado para los servos** con GND común + **capacitor 470–1000 µF** entre V+
  y GND de los servos para absorber el inrush.

Este límite de poder bloquea features de movimiento fuerte (baile, coreografías) y la
traslación con motores — ver [ROADMAP.md](ROADMAP.md) y [LOCOMOTION.md](LOCOMOTION.md).
