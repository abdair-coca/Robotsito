# Diario de Ejecución y Bitácora de Migración — Robot Bob

Este documento es el registro vivo de la migración de Robot Bob hacia la arquitectura **PWA Local-First (C++ / Arduino sobre ESP-IDF + React ONNX)**.
Registra el progreso paso a paso, pruebas realizadas, aciertos, errores/desaciertos, decisiones de ingeniería y correcciones aplicadas.

---

## 📋 Resumen de Estado por Fase

| Fase | Estado | Fecha Inicio | Fecha Fin | Observaciones |
|---|---|---|---|---|
| **Fase 0: Fundamentos (C++)** | 🟢 Completada | 2026-07-22 | 2026-07-22 | Subdominios DuckDNS, Certificados SSL ACME v2, resolución DNS LAN y proyectos base C++ creados. |
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

#### [2026-07-22] Paso 3: Verificación de Resolución DNS Local (DuckDNS vs IP Privada LAN)
- **Objetivo:** Probar que los subdominios DuckDNS (`bobcreeper.duckdns.org` y `bobcreeper-cam.duckdns.org`) resuelven correctamente hacia las IPs privadas locales (`192.168.X.Y`) asignadas a los ESP32.
- **Acción:**
  - IPs obtenidas vía `discovery.py`: DevKit = `192.168.0.22`, CAM = `192.168.0.21`.
  - Creación del script [tools/update_duckdns.py](file:///c:/Users/abdai/Desktop/RobotCreeper/scripts/tools/update_duckdns.py) para actualizar las entradas en DuckDNS.
  - Verificación mediante `Resolve-DnsName`:
    - `bobcreeper.duckdns.org` → `192.168.0.22` (TTL 60s)
    - `bobcreeper-cam.duckdns.org` → `192.168.0.21` (TTL 60s)
- **Aciertos:** **Fase 0 completada con éxito.** La resolución de nombres de dominio locales apuntando a las IPs de la LAN privada funciona al 100%, garantizando que la PWA y los navegadores móviles reconozcan el nombre SSL sin bloqueos.



