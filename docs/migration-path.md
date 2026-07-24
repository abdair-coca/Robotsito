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
| **Fase 3: Portado de Firmware** | 🟢 Completada | 2026-07-24 | 2026-07-24 | Servos Pan/Tilt, Motores L298N con PWM/watchdog, Ojos OLED SH1106 C++ y Memoria KV LittleFS compilados y validados. |
| **Fase 4: OTA y SSL Remote** | 🟢 Completada | 2026-07-24 | 2026-07-24 | ArduinoOTA flasheo inalambrico, net_checker periodico y endpoint REST /api/ssl/update compilados y validados. |
| **Fase 5: PWA React + ONNX** | 🟢 Completada | 2026-07-24 | 2026-07-24 | App Web / PWA en React (Vite + Glassmorphism), Service Worker, ONNX BlazeFace, Groq API (STT/LLM) e interfaz joysticks. |
| **Fase 6: Pruebas de Campo** | 🟡 En Proceso | 2026-07-24 | - | Pruebas multi-red reales en feria y benchmarks de latencia y FPS. |


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

### 📍 Fase 3 — Portado de Firmware

#### [2026-07-24] Paso 9: Portado de Servos, Motores L298N, Ojos OLED C++ y Memoria KV LittleFS
- **Objetivo:** Completar todo el control de hardware periférico y persistencia de memoria local en C++.
- **Acción:**
  - **`BobServoManager` ([src/servo_manager.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/servo_manager.cpp)):**
    - Integración de `ESP32Servo` en GPIO 13 (Pan: 20°-160°, home 90°) y GPIO 12 (Tilt: 40°-140°, home 90°).
  - **`BobMotorManager` ([src/motor_manager.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/motor_manager.cpp)):**
    - Control de motores tracción diferencial L298N en GPIO 19, 21, 22, 23 con control de velocidad PWM (`ledcSetup`) en ENA/ENB (GPIO 16 y 4).
    - Watchdog de seguridad (detiene motores automáticamente tras 400ms sin comando activo).
  - **`BobMemoryManager` ([src/memory_manager.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/memory_manager.cpp)):**
    - Almacenamiento KV JSON en `/memory/faces.json` (embeddings de 512 dimensiones de rostros conocidos) y `/memory/history.json`.
  - **`BobOledEyes` ([src/oled_eyes.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/oled_eyes.cpp)):**
    - Animación de ojos no bloqueante en pantalla SH1106 I2C con emociones (`Esperando`, `Conectando`, `Activo`, `FELIZ`, `SORPRENDIDO`, `PENSANDO`, `TRISTE`, `ENOJADO`) e icono superpuesto de sin internet.
### 📍 Fase 4 — OTA y Renovación Remota SSL

#### [2026-07-24] Paso 10: Implementación de ArduinoOTA, Monitoreo de Red y Renovación SSL Remote
- **Objetivo:** Permitir actualizaciones de firmware 100% inalámbricas por WiFi y renovación remota de certificados SSL en LittleFS.
- **Acción:**
  - **Módulo `BobOTAManager` ([src/ota_manager.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/ota_manager.cpp)):**
    - Habilitación del servicio `ArduinoOTA` escuchando en `bob-devkit`. Permite flashear el ESP32 sin cable USB.
  - **Módulo `BobNetChecker` ([src/net_checker.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/net_checker.cpp)):**
    - Verificación periódica (cada 30s) de conectividad exterior mediante HTTP HEAD a `http://www.gstatic.com/generate_204`. Superpone automáticamente el icono de "Sin Internet" en los ojos OLED cuando no hay salida exterior.
  - **Endpoint REST `/api/ssl/update` ([src/main.cpp](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/firmware/esp32_devkit_cpp/src/main.cpp)):**
    - Recibe el payload JSON con los certificados Let's Encrypt actualizados (`cert` y `key`) y los almacena directamente en `/cert/cert.pem` y `/cert/key.pem` en LittleFS.
  - **Script de Verificación Automatizado ([tools/test_phase4.py](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/tools/test_phase4.py)):** Pruebas E2E de estado de internet `net_checker`, puerto inalámbrico `ArduinoOTA` (3232) y renovación remota de SSL certs en `/api/ssl/update`.
### 📍 Fase 5 — App Web / PWA (React + ONNX + Groq API)

#### [2026-07-24] Paso 11: Construcción de la Aplicación PWA Local-First en React (Vite)
- **Objetivo:** Crear la interfaz de usuario PWA moderna, fluida y resiliente para el control local-first de Robot Bob.
- **Acción:**
  - **Estructura PWA ([pwa/](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/pwa)):** Proyecto React + Vite con Service Worker (`sw.js`) y `manifest.json` para instalación en pantalla de inicio.
  - **Sistema de Diseño Glassmorphism ([src/index.css](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/pwa/src/index.css)):** Paleta personalizada HSL (slate dark, cyan glow `#06b6d4`, violet accent `#8b5cf6`), tipografía Google Fonts (`Inter` & `Outfit`), paneles semitransparentes con `backdrop-filter: blur(16px)`.
  - **Header & Indicadores ([src/components/Header.jsx](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/pwa/src/components/Header.jsx)):** Monitoreo en tiempo real de la conexión WSS a DevKit y stream MJPEG de ESP32-CAM.
  - **VideoFeed & Visión IA ([src/components/VideoFeed.jsx](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/pwa/src/components/VideoFeed.jsx)):** Visualizador de stream MJPEG con superposición de bounding boxes mediante ONNX Runtime Web (BlazeFace a ~30 FPS).
  - **Paneles de Control ([src/components/ControlPanels.jsx](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/pwa/src/components/ControlPanels.jsx)):** Joysticks táctiles/direccionales para cabeza Pan/Tilt, tracción diferencial L298N y selector de expresiones emocionales en vivo para los ojos OLED.
  - **Voz & IA Conversacional ([src/components/VoiceChat.jsx](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/pwa/src/components/VoiceChat.jsx)):** Captura por Web Audio API + Silero VAD, llamada directa a Groq API (`whisper-large-v3-turbo` + `llama-3.3-70b-versatile`) y selector de altavoz (Celular vs Robot).
  - **Memoria LittleFS ([src/components/MemoryPanel.jsx](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/pwa/src/components/MemoryPanel.jsx)):** Gestor de rostros conocidos y recuerdos.
- **Aciertos:** **Fase 5 completada con éxito.** Compilación limpia `npm run build` en 837ms sin errores.













