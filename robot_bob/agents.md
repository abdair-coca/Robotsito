# Robot Bob — Playbook de Interactividad por WiFi

Guía de trabajo para llevar **toda** la interactividad de Bob por WiFi hasta que
rinda **igual que por cable**. Por cable (USB COM3 para control + ESP32-CAM en WiFi)
el sistema va perfecto; el objetivo es replicar ese rendimiento sin cables de control.

> Este archivo es el doc de referencia del esfuerzo WiFi. El contexto general del
> proyecto está en `../agents.md` (raíz). Arquitectura/fases en `AgentsGoal.md`.

---

## ▶ Plan de ejecución gateado (un paso a la vez)

**Regla de oro:** ejecutar **UN** paso, el usuario lo testea, reporta el resultado,
y **recién con su feedback** se decide avanzar, repetir o ramificar. No encadenar pasos.
No asumir éxito. Cada paso deja evidencia que descarta una causa.

### 🎯 Objetivo 1 (actual): conectar a la cámara por WiFi

Premisa: el stream ya debería funcionar por WiFi (el CAM está vivo, responde a ping).
Hay que aislar **dónde** se rompe: CAM, red, o código de la laptop.

> ⚠️ El CAM sirve **un solo cliente a la vez**. Mientras pruebas el stream en el
> navegador, NINGÚN otro proceso (otro tab, `main.py`, un test) puede estar conectado.
> Cerrar todo lo demás antes de cada prueba.

| Paso | Acción (la hace el usuario) | Qué observar / reportar |
|---|---|---|
| **1** | **Power-cycle del CAM** (desenchufar/enchufar, esperar ~10 s). Luego abrir en el navegador de la laptop `http://192.168.0.22:81/stream` | ¿Se ve video, sale spinner infinito, o error? Reportar cuál. |
| **2** | Si paso 1 falla: ver la **IP real del CAM** en el admin del router (clientes DHCP) o en el monitor serie Arduino al bootear | ¿La IP es `192.168.0.22` u otra? Anotar la real. |
| **3** | Con el CAM confirmado en navegador, **cerrar el navegador** y correr el test aislado: `python tests/test_2b_stream_only.py` | ¿Conecta y muestra frames, o timeout? Pegar la salida. |
| **4** | Si el test aislado falla pero el navegador anduvo: el agente prepara un **probe TCP mínimo** (solo `socket.connect` + primer JPEG, sin OpenCV/MediaPipe) para medir el `connect` | Correr el probe que genere el agente y pegar resultado. |
| **5** | Si aislado anda pero `main.py` no: correr `python main.py` y observar el orden de los logs | ¿En qué línea se cuelga? Pegar los primeros ~15 logs. |

Cada paso confirma o descarta una hipótesis:
- Paso 1 ✅ → CAM y red OK; el problema vive en el código/contención de la laptop.
- Paso 1 ❌ → problema de CAM/red; el código no es la causa (pasos 2).
- Paso 3 ✅ pero 5 ❌ → contención con el socket de control u orden de arranque.

> Objetivos siguientes (NO empezar hasta cerrar el 1): **Obj 2** seguimiento fluido por
> WiFi (FPS + lag ≈ cable) · **Obj 3** estabilidad 15 min sin caídas · **Obj 4** (opcional)
> audio del DevKit por WiFi. Detalle de cada uno se agrega aquí cuando llegue su turno.

---

## 1. Topología — qué va por dónde

| Enlace | Dispositivo | Puerto | Transporte | Editable |
|---|---|---|---|---|
| Control servos + OLED | ESP32 DevKit `192.168.0.23` | TCP **5007** (WiFi) / **COM3** (USB) | texto: `H:p,V:t\n`, `ESTADO:X\n`, `SIGUIENDO:dx,dy\n` | ✅ firmware `../Esp32/main.py` (MicroPython) |
| Video MJPEG | ESP32-CAM `192.168.0.22` | TCP **81** `/stream` | HTTP multipart JPEG | ❌ **firmware Arduino/C, inmutable** |
| Audio mic/speaker | ESP32 DevKit `192.168.0.23` | TCP **5005**/**5006** | uint8 8 kHz crudo | ✅ firmware `../Esp32/main.py` (hoy en laptop) |

**Restricción dura:** el **ESP32-CAM corre firmware Arduino/C que NO se puede editar**.
`Esp32ThinkerAICam/main.py` (MicroPython) NO es lo que corre — es un experimento viejo,
ignóralo. Toda mejora de cámara/stream va del lado **laptop** (`facial_tracker.py`) o de
**red** (router/DHCP/power-cycle), jamás tocando el CAM.

---

## 2. Estado del problema (2026-06-18)

Síntoma al arrancar `main.py` con control por WiFi:
```
[serial] Conectado WiFi 192.168.0.23:5007      ← control WiFi OK
[stream] No conecta (timed out). Reintento...  ← CAM :81 falla
ERROR: No llegan frames de la cámara.
```

Datos de diagnóstico:
- `ping 192.168.0.22` y `.23` → **ambos responden** (dispositivos vivos en la red).
- **Pero el ping tiene picos de 300–470 ms** (RTT normal 4–14 ms) → WiFi muy
  congestionado/jittery, justo desde que el DevKit también usa WiFi.
- El stream falla en `connect()` (TCP), no en lectura → el CAM no acepta la conexión.

### Causas raíz (ordenadas por probabilidad)
1. **CAM con `accept()` atascado.** El CameraWebServer Arduino sirve pocos clientes;
   si un run anterior murió sin cerrar el TCP (Ctrl-C / crash), el socket queda
   medio-abierto y el CAM no acepta nuevos clientes hasta que expira el keepalive
   (minutos) o se reinicia. **Fix inmediato: power-cycle del CAM.**
2. **Jitter/congestión WiFi.** Picos de 470 ms rompen el `connect` (timeout 5 s a veces
   no alcanza bajo ráfaga) y degradan FPS. Causa: red compartida 2.4 GHz saturada,
   señal débil, o interferencia. **Fix: red dedicada + buena señal (ver §4).**
3. **IP por DHCP que cambió.** Si el router reasigna IPs, `.22`/`.23` dejan de coincidir
   con `config.py`. **Fix: IP fija (ver §3).**

---

## 3. IP fija — eliminar el drift de DHCP

`config.py` de la laptop tiene las IPs hardcodeadas (`CONTROL_IP=192.168.0.23`,
`IP_ESPCAM=192.168.0.22`). Si el router las cambia, todo falla. Dos caminos:

- **DevKit (.23) — IP estática en firmware (ya soportado).** `../Esp32/main.py` fija
  `STATIC_IP` si el `config.py` del dispositivo la define. Agregar al `Esp32/config.py`
  **del dispositivo** y re-subir:
  ```python
  STATIC_IP = '192.168.0.23'
  GATEWAY   = '192.168.0.1'      # IP del router
  SUBNET    = '255.255.255.0'
  DNS       = '8.8.8.8'
  ```
- **CAM (.22) — NO se puede tocar el firmware.** Usar **reserva DHCP en el router**:
  entrar al admin del router → DHCP → reservar la MAC del ESP32-CAM a `192.168.0.22`.
  (Recomendado hacer lo mismo para el DevKit por si la IP estática del firmware fallara.)

> Para ver la MAC/IP real del CAM: admin del router (lista de clientes) o monitor serie
> Arduino al bootear. Para el DevKit: Thonny por USB muestra `IP del ESP32: ...`.

---

## 4. Red WiFi — bajar el jitter a nivel cable

El enemigo #1 de la paridad es el jitter (picos 470 ms). Objetivo: RTT estable < 20 ms.

- **Router/AP dedicado para el robot** (no la red de la feria/casa con 30 dispositivos).
  Un router de viaje o el celular en hotspot 2.4 GHz dedicado sirve.
- **Banda 2.4 GHz** (los ESP32 no son 5 GHz). Fijar un **canal limpio** (1, 6 u 11) —
  escanear con una app de WiFi y elegir el menos saturado.
- **Señal fuerte:** los dos ESP32 a < 3–5 m del AP, sin paredes gruesas. El CAM en VGA
  satura ancho de banda; con señal débil colapsa primero.
- **Sin AP/client isolation:** algunos routers aíslan clientes entre sí → la laptop no
  alcanza a los ESP32 aunque haya WiFi. Desactivar "AP isolation".
- **Power-save WiFi OFF** ya está en el firmware del DevKit (`pm=PM_NONE`, `txpower=13`).
- **Mantener el audio en la laptop** (`USE_ROBOT_MIC/SPEAKER=False`). El stream de audio
  8 kHz por TCP compite con el MJPEG del CAM en la misma banda; activarlo agrava el jitter.
  El control (5007) es trafico mínimo (~12 cmd/s), ese sí va por WiFi sin costo.

---

## 5. Procedimiento de arranque por WiFi

1. **Power-cycle del ESP32-CAM** (desenchufar/enchufar) — limpia cualquier `accept()`
   atascado de un run anterior. Hacerlo SIEMPRE antes de arrancar si el stream falló.
2. Confirmar que el DevKit booteó y tomó su IP (Thonny o ping `.23`).
3. Confirmar CAM arriba: `ping 192.168.0.22` estable, y en navegador
   `http://192.168.0.22:81/stream` muestra video. Si el navegador tampoco abre → power-cycle CAM.
4. `config.py`: `USE_WIFI_SERIAL=True`, `USE_ROBOT_MIC=False`, `USE_ROBOT_SPEAKER=False`.
5. `python main.py` (con `venv311` activo). Cerrar SIEMPRE con **Q** (no Ctrl-C) para que
   los sockets cierren limpios y el CAM no quede atascado para el próximo run.
6. Existen skills `bob-run` / `bob-diagnose` para el flujo guiado y el diagnóstico.

---

## 6. Troubleshooting

| Síntoma | Causa probable | Acción |
|---|---|---|
| `[stream] No conecta (timed out)` pero ping `.22` responde | CAM con `accept()` atascado de un run previo | **Power-cycle del CAM**. Cerrar runs con Q, no Ctrl-C. |
| ping `.22` no responde | CAM caído / no en WiFi / IP cambió | Power-cycle CAM; verificar IP real en router; reserva DHCP. |
| `[serial] No conecta WiFi ...:5007` | DevKit caído / IP ≠ .23 / firmware sin servidor 5007 | Ver IP real (Thonny); fijar STATIC_IP; re-subir `Esp32/main.py`. |
| Stream conecta pero FPS bajo / se corta | Jitter/congestión WiFi, señal débil | Red dedicada, canal limpio, acercar ESP32 al AP (§4). |
| Servos a tirones por WiFi | Coalescing OK en firmware, pero jitter alto | Mejorar red; el firmware ya difiere al último `H:` por lote. |
| Funciona unos minutos y muere el stream | Reconexión MJPEG bajo WiFi ruidoso | El tracker reconecta solo; si no, power-cycle CAM + mejorar señal. |

**Fallback siempre disponible:** poner `USE_WIFI_SERIAL=False` → control por **USB COM3**
(rendimiento perfecto conocido). La arquitectura por cable NO se elimina nunca; es el
patrón de oro contra el que se compara el WiFi.

---

## 7. Lo que ya está hecho (código)

- **DevKit IP estática:** `../Esp32/main.py` aplica `STATIC_IP` si el config la define.
- **SerialManager WiFi→USB→none:** `serial_manager.py` cae a COM3 si el WiFi falla, y a
  "solo visión" si no hay ESP32. Control 5007 ya conecta OK.
- **Tracker robusto:** `facial_tracker.py` `LectorStream` lee el MJPEG por socket crudo
  (evita crash de FFmpeg), reconecta solo, descarta buffers viejos, timeout 5 s.
- **Firmware DevKit:** `pm=PM_NONE` + `txpower=13` para WiFi estable; control por WiFi y
  USB en paralelo (mismo parser `aplicar_cmd`).

## 8. Pendiente para cerrar la paridad

1. **Reserva DHCP** de `.22` (CAM) y `.23` (DevKit) en el router — elimina el drift.
2. **STATIC_IP** en el `Esp32/config.py` del dispositivo + re-subir firmware.
3. **Red dedicada 2.4 GHz** canal limpio, ESP32 con buena señal → matar el jitter de 470 ms.
4. Validar 15+ min de stream estable + seguimiento fluido + conversación, sin caídas.
5. (Opcional) Subir el connect-timeout del stream o separar connect-timeout de read-timeout
   en `LectorStream` si la red sigue con ráfagas. Solo si los pasos 1–3 no bastan.

## 9. Criterio de éxito (WiFi == cable)

- [ ] `main.py` arranca por WiFi sin un solo power-cycle manual en 5 arranques seguidos.
- [ ] FPS de seguimiento ≈ el de cable (~20–25), sin tirones perceptibles.
- [ ] Servos siguen la cara con el mismo lag que por USB.
- [ ] 15 min de demo continua sin caída de stream ni de control.
- [ ] Cerrar y reabrir no deja el CAM atascado.
