# Diario de Ejecución y Bitácora de Migración — Robot Bob

Este documento es el registro vivo de la migración de Robot Bob hacia la arquitectura **PWA Local-First (C++ / Arduino sobre ESP-IDF + React ONNX)**.
Registra el progreso paso a paso, pruebas realizadas, aciertos, errores/desaciertos, decisiones de ingeniería y correcciones aplicadas.

---

## 📋 Resumen de Estado por Fase

| Fase | Estado | Fecha Inicio | Fecha Fin | Observaciones |
|---|---|---|---|---|
| **Fase 0: Fundamentos (C++)** | 🟢 Completada | 2026-07-22 | 2026-07-22 | Subdominios DuckDNS, Certificados SSL ACME v2, resolución DNS LAN y proyectos base C++ creados. |
| **Fase 1: Red y Captive Portal** | 🟢 Completada | 2026-07-22 | 2026-07-24 | Flasheo exitoso, WiFiManager (NVS + SoftAP + Portal Cautivo) y OLED QR (SH1106 + mDNS) validados. |
| **Fase 2: Seguridad y API C++** | 🟢 Completada | 2026-07-24 | 2026-07-24 | Token único de pairing en LittleFS, servidor WSS `/ws` asíncrono y REST `/api/info` compilados y validados. |
| **Fase 3: Portado de Firmware** | 🟡 En Proceso | 2026-07-24 | - | Portar control de servos Pan/Tilt, animaciones OLED SH1106 C++ y memoria KV JSON en LittleFS. |
| **Fase 4: OTA y SSL Remote** | ⚪ Pendiente | - | - | ArduinoOTA doble partición y subida de certs. |
| **Fase 5: PWA React + ONNX** | ⚪ Pendiente | - | - | PWA en React, BlazeFace/MobileFaceNet, Web Audio + Groq API. |
| **Fase 6: Pruebas de Campo** | ⚪ Pendiente | - | - | Pruebas multi-red reales y benchmarks FPS/latencia. |

---

## 📝 Diario de Ejecución Detallado

### 📍 Fase 0 — Fundamentos (C++)

#### [2026-07-22] Paso 1: Configuración de Dominios DuckDNS y Estrategia SSL (Certbot DNS-01)
- **Objetivo:** Definir los 2 subdominios DuckDNS fijos y el procedimiento para emitir certificados SSL mediante desafío DNS-01 sin abrir puertos públicos hacia internet.
- **Acción:**
  - Subdominio DevKit: `bobcreeper.duckdns.org`
  - Subdominio CAM: `bobcreeper-cam.duckdns.org`
  - Procedimiento: Ejecución de `certbot` con el plugin `certbot-dns-duckdns` desde la laptop del usuario.
- **Aciertos:**
  - El desafío DNS-01 genera registros TXT en los servidores de DuckDNS para validar el dominio ante Let's Encrypt. Esto permite generar certificados SSL 100% válidos para dominios que resuelven a IPs privadas de LAN (`192.168.X.Y`).
  - **Emisión Exitosa ([tools/issue_certs_acme.py](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/tools/issue_certs_acme.py)):** Se implementó un cliente ACME v2 puro en Python que negoció con Let's Encrypt y DuckDNS, generando exitosamente `fullchain.pem` y `privkey.pem` para `bobcreeper.duckdns.org` y `bobcreeper-cam.duckdns.org` sin requerir elevación de privilegios de Administrador en Windows.
  - **Copia a Firmware ([tools/copy_certs_to_firmware.py](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/tools/copy_certs_to_firmware.py)):** Se formatearon y copiaron las llaves y certificados a `firmware/esp32_devkit_cpp/data/cert/` y `firmware/esp32_cam_cpp/data/cert/` para su incrustación en la memoria flash LittleFS.
- **Desaciertos / Lecciones:**
  - Certbot nativo en Windows exige ser ejecutado desde un shell con permisos de Administrador para crear carpetas en `C:\Certbot`. Se resolvió usando el módulo `acme` + `cryptography` de Python directamente en `tools/issue_certs_acme.py`.


#### [2026-07-22] Paso 2: Creación de la Estructura de Proyectos C++ para DevKit y CAM
- **Objetivo:** Iniciar la estructura compilada C++ (Arduino framework sobre ESP-IDF) para los dos microcontroladores en `firmware/esp32_devkit_cpp/` y `firmware/esp32_cam_cpp/`.
- **Acción:**
  - **DevKit ([firmware/esp32_devkit_cpp/](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp)):**
    - `platformio.ini`: Configurado para board `esp32dev` con sistema de archivos `LittleFS` y librerías `ESPAsyncWebServer`, `AsyncTCP`, `ArduinoJson`.
    - `src/main.cpp`: Implementado monitoreo de Heap (`ESP.getFreeHeap()`, `ESP.getMinFreeHeap()`), montaje de `LittleFS`, manejador de eventos WSS en `/ws` y endpoint REST `/api/info`.
  - **CAM ([firmware/esp32_cam_cpp/](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_cam_cpp)):**
    - `platformio.ini`: Configurado para board `esp32cam` con particionado `huge_app.csv`.
    - `src/main.cpp`: Mapeo completo de pines GPIO para sensor OV2640 (AI-Thinker), autodetección de PSRAM (VGA q=10 vs SVGA q=12) e inicialización de cámara.
- **Aciertos:**
  - La separación en dos proyectos C++ limpios aísla los recursos del DevKit (servos/OLED/WSS/LittleFS) y los de la CAM (stream MJPEG/OV2640).
  - `ESPAsyncWebServer` en C++ opera de forma no bloqueante por eventos, evitando que el tráfico WSS afecte la respuesta fluida de los servos Pan/Tilt.
- **Desaciertos / Lecciones:**
  - `pio` (PlatformIO CLI) debe instalarse en el entorno local para compilaciones y flasheos automáticos desde consola.

### 📍 Fase 1 — Red y Captive Portal

#### [2026-07-22] Paso 4: Implementación de WiFiManager, NVS y Portal Cautivo HTTP en C++
- **Objetivo:** Crear el módulo de gestión de red autónoma para el ESP32 DevKit.
- **Acción:**
  - **Módulo `BobWiFiManager` ([src/wifi_manager.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/wifi_manager.cpp)):**
    - Persistencia en NVS mediante `Preferences.h` para almacenar y consultar pares (SSID, Password).
    - Intento automático de conexión secuencial a redes guardadas.
    - Caída a modo **SoftAP** (SSID: `Bob-Setup`, IP: `192.168.4.1`) e inicialización de `DNSServer` redirigiendo todas las consultas al portal cautivo.
    - Interfaz web del Portal Cautivo inyectada por HTML en memoria flash (`CAPTIVE_PORTAL_HTML`).
    - Actualización automática de IP en DuckDNS vía HTTP GET tras conectarse.
- **Aciertos:** Permite aprovisionar a Bob en cualquier red sin compilar o flashear credenciales hardcodeadas.

#### [2026-07-22] Paso 6: Guía de Pruebas End-to-End (SoftAP + Captive Portal + NVS + OLED QR)
- **Objetivo:** Verificar físicamente en el ESP32 DevKit el flujo completo de aprovisionamiento de red.
- **Procedimiento de Prueba:**
  1. **Flasheo:** Abrir `firmware/esp32_devkit_cpp` en VS Code (PlatformIO) y flashear al ESP32 DevKit por USB.
  2. **Prueba SoftAP (Sin red):** El OLED muestra el QR para `http://192.168.4.1`. Al conectarse a la WiFi `Bob-Setup` desde un celular, salta el Portal Cautivo HTTP donde se ingresa la contraseña de la WiFi de casa.
  3. **Prueba NVS (Conexión automática):** El ESP32 guarda la red en NVS, se reinicia, se conecta a la WiFi local y llama a la API de DuckDNS.
  4. **Prueba OLED QR & REST:** El OLED renderiza el QR para `https://bobcreeper.duckdns.org`. Al escanear el ojo de Bob se abre la PWA/IP, y `http://192.168.X.Y/api/info` responde el estado del robot.
- **Aciertos:** Validación física del ciclo completo de red sin cables.

### 📍 Fase 2 — Seguridad y API C++

#### [2026-07-24] Paso 7: Implementación de Módulo AuthManager (Token Único de Pairing en LittleFS)
- **Objetivo:** Garantizar la regla de "un solo dispositivo vinculado activo a la vez" mediante la persistencia del token en LittleFS.
- **Acción:**
  - **Módulo `BobAuthManager` ([src/auth_manager.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/auth_manager.cpp)):**
    - Almacena `/auth.json` en LittleFS con esquema `{ "token": "...", "device_name": "...", "updated_at": ... }`.
    - `generateToken(deviceName)`: Genera un token aleatorio seguro de 32 caracteres usando el generador de números aleatorios por hardware (`esp_random()`), revocando automáticamente cualquier dispositivo anterior.
    - `validateToken(token)`: Valida si la solicitud WSS/HTTP coincide con el token activo.
    - `revokeToken()`: Elimina el archivo `/auth.json` y destruye la sesión activa.
- **Aciertos:** Compilado y validado en C++ (RAM: 15.6%, Flash: 81.4%).

#### [2026-07-24] Paso 8: Implementación de Servidor WSS Asíncrono de Comandos en C++
- **Objetivo:** Recibir comandos de control de baja latencia (<10ms) y gestionar la autenticación por WebSocket (`/ws`).
- **Acción:**
  - Integración de `ESPAsyncWebServer` WSS en [src/main.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/main.cpp).
  - Manejo de flujo JSON:
    - `action: "pair"`: Recibe `device_name`, revoca cualquier sesión anterior notificando vía WSS `{"type":"revoked"}`, genera nuevo token y responde `{"status":"paired", "token":"..."}`.
    - `action: "auth"`: Valida el token del cliente contra LittleFS.
    - `action: "cmd"`: Valida el token y procesa comandos de servos (`pan`/`tilt`), ojos OLED (`val`) y motores (`izq`/`der`).
  - Endpoint REST `/api/info` que informa el dispositivo actualmente vinculado (`paired_device`) y estado de memoria.
  - **Script de Verificación Automatizado ([tools/test_phase2.py](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/tools/test_phase2.py)):** Creado script para probar E2E la REST API, pairing de token único, envío de comandos y revocación en tiempo real.
- **Aciertos:** **Fase 2 completada exitosamente.** Compilación limpia en C++ con uso de RAM de solo 15.7% (51.3 KB) y Flash 83.9%.








