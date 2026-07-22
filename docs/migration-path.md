# Diario de Ejecución y Bitácora de Migración — Robot Bob

Este documento es el registro vivo de la migración de Robot Bob hacia la arquitectura **PWA Local-First (C++ / Arduino sobre ESP-IDF + React ONNX)**.
Registra el progreso paso a paso, pruebas realizadas, aciertos, errores/desaciertos, decisiones de ingeniería y correcciones aplicadas.

---

## 📋 Resumen de Estado por Fase

| Fase | Estado | Fecha Inicio | Fecha Fin | Observaciones |
|---|---|---|---|---|
| **Fase 0: Fundamentos (C++)** | 🟡 En Proceso | 2026-07-22 | - | Subdominios DuckDNS, estructura base C++ (DevKit + CAM) y verificación de RAM budget/cámara. |
| **Fase 1: Red y Captive Portal** | ⚪ Pendiente | - | - | SoftAP, NVS, mDNS y QR en OLED. |
| **Fase 2: Seguridad y API C++** | ⚪ Pendiente | - | - | Token único, WSS, HTTPS stream y chequeo de internet. |
| **Fase 3: Portado de Firmware** | ⚪ Pendiente | - | - | Servos, OLED SH1106 C++, LittleFS JSON memory. |
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
- **Aciertos:** El desafío DNS-01 genera registros TXT en los servidores de DuckDNS para validar el dominio ante Let's Encrypt. Esto permite generar certificados SSL 100% válidos para dominios que resuelven a IPs privadas de LAN (`192.168.X.Y`).
- **Guía de Creación en DuckDNS:**
  1. Ingresar a [https://www.duckdns.org](https://www.duckdns.org) e iniciar sesión (con Google, GitHub, etc.).
  2. Copiar el **token de la cuenta** (necesario para Certbot y para que el ESP32 actualice su IP).
  3. En el campo *subdomain*, registrar `bobcreeper` y hacer clic en *add domain*.
  4. En el campo *subdomain*, registrar `bobcreeper-cam` y hacer clic en *add domain*.
- **Automatización creada ([tools/generate_certs.py](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/tools/generate_certs.py)):**
  - Se instalaron `certbot` y `certbot-dns-duckdns` en el entorno virtual Python `robot_bob/venv311/`.
  - Se optimizó `tools/generate_certs.py` usando las banderas `--config-dir certs/config`, `--work-dir certs/work` y `--logs-dir certs/logs`. Esto permite generar y guardar los certificados SSL (`fullchain.pem` y `privkey.pem`) directamente en la carpeta local `certs/config/live/` sin requerir permisos de Administrador en Windows.
- **Desaciertos / Lecciones:** Se evitó el requisito de permisos de administrador en Windows al redireccionar las carpetas por defecto de Certbot (`C:\Certbot`) a la carpeta del proyecto `certs/`.

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
