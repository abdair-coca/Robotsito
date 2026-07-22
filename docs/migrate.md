# Plan de Migración Definitivo — Robot Bob: PWA Local-First & Dispositivo Único

Este documento define la arquitectura final, reglas de vinculación, estados visuales, configuración, decisiones técnicas de firmware/seguridad y la hoja de ruta de implementación fase por fase para la migración de **Robot Bob** hacia un modelo **PWA + ESP32 Local-First sin servidor**.

---

## 🏗️ 1. Arquitectura de Hardware y Conexiones Duales

```
                               ┌────────────────────────────────────────────────────────┐
                               │                 ESP32 DevKit (C++)                     │
                               │  • Subdominio 1: bobcreeper.duckdns.org                │
                               │  • Servidor WSS / HTTPS + ArduinoOTA                   │
                               │  • Almacena Identidad, GROQ_API_KEY y SSL en LittleFS  │
                               │  • Controla Servos, OLED, Motores y Audio PAM8403      │
                               └──────────────────────────▲─────────────────────────────┘
                                                          │
                            Conexión 1: WSS (Comandos/Memoria/Auth) en LAN
                                                          │
┌──────────────────────────┐                              │
│ Dispositivo PWA (Cliente)│◄─────────────────────────────┤
│ (Celular/Tablet/Laptop)  │                              │
└──────────────────────────┘                              │
                            Conexión 2: HTTPS (Stream MJPEG) en LAN
                                                          │
                               ┌──────────────────────────▼─────────────────────────────┐
                               │                 ESP32-CAM (C++)                        │
                               │  • Subdominio 2: bobcreeper-cam.duckdns.org            │
                               │  • Servidor HTTPS de Video Stream (MJPEG)              │
                               └────────────────────────────────────────────────────────┘
```

### Reglas de Vinculación y Conexiones Duales
* **Doble Conexión PWA:** La PWA mantiene dos conexiones SSL independientes en la LAN:
  1. **Conexión WSS al DevKit (`bobcreeper.duckdns.org:8080`):** Comandos, telemetría, audio y sincronización de memoria.
  2. **Conexión HTTPS al CAM (`bobcreeper-cam.duckdns.org:8443`):** Stream de vídeo MJPEG.
* **Token Único Activo:** El DevKit mantiene un único token de sesión activo guardado en LittleFS junto al nombre del dispositivo (`device_name`).
* **Revocación Automática:** Vincular un dispositivo nuevo revoca la sesión previa mostrando la advertencia *"Esto va a desvincular a [dispositivo actual]"*.

---

## 💻 2. Migración del Firmware: MicroPython → C++ (Arduino sobre ESP-IDF)

Para evitar desbordamientos de RAM (MemoryError) provocados por el overhead de MicroPython y la carga de mbedTLS:
* **Entorno:** C++ con el framework Arduino sobre ESP-IDF.
* **Componentes Clave:**
  * `ESPAsyncWebServer`: Servidor HTTP y WebSockets (WSS) totalmente asíncrono y no bloqueante (garantiza que el refresco de servos y el control de motores no se detengan por operaciones de red).
  * `WiFiClientSecure` / `mbedTLS`: Manejo de TLS/SSL local.
  * `ArduinoOTA`: Actualización remota de firmware.
  * `LittleFS`: Sistema de archivos para memoria, credenciales, certs SSL y token de pairing.

---

## 🔒 3. Gestión de Certificados SSL y Claves de API

### Certificados SSL (DuckDNS + Certbot)
* **Subdominios Fijos:** `bobcreeper.duckdns.org` (DevKit) y `bobcreeper-cam.duckdns.org` (CAM). DuckDNS actualiza la IP local dinámica de cada ESP32 al conectarse a la red.
* **Generación:** Certbot con el plugin `certbot-dns-duckdns` ejecutado desde la laptop del usuario cada ~80 días.
* **Carga Inicial:** Los archivos `cert.pem` y `key.pem` se incluyen en la imagen de LittleFS y se suben vía cable USB durante el primer flasheo.
* **Renovación por Red (Over-the-Air):** Endpoint HTTPS en la PWA (*"Actualizar certificado SSL"*) protegido con el token de pairing para subir los nuevos certificados por LAN directamente a LittleFS sin conectar cables.

### Clave de API de Groq (`GROQ_API_KEY`)
* **Ubicación:** La clave vive almacenada en el LittleFS del ESP32 DevKit (no en el `localStorage` del navegador).
* **Configuración:** Campo de solo-escritura en el panel de configuración de la PWA.
* **Acceso:** La PWA consulta la API Key al autenticarse con el token de pairing. Esto permite controlar a Bob desde cualquier dispositivo nuevo sin tener que ingresar la clave a mano.

---

## 🔊 4. Altavoz Configurable: PWA vs. Robot

La salida de audio de la voz de Bob es seleccionable desde el panel de la PWA:
* **Modo Dispositivo PWA (Default):** La voz (Edge-TTS / Web Speech) se reproduce por los altavoces del teléfono/tablet/laptop a 44.1 kHz alta fidelidad.
* **Modo Altavoz de Bob:** La PWA transmite o dispara la reproducción a través del parlante PAM8403 del ESP32 para presentaciones, ferias o demos donde la audiencia alrededor de Bob deba escuchar su voz.

---

## 👁️ 5. Estados Visuales del OLED (SH1106)

| Estado | Expresión Visual | Cuándo Ocurre |
|---|---|---|
| **Esperando** | Ojos abiertos, relajados, parpadeo lento (cada 4–6 s) | Robot encendido, nadie vinculado. |
| **Conectando** | Ojos moviéndose de lado a lado ("buscando") | Un dispositivo está realizando el proceso de pairing. |
| **Activo** | Ojos alertas, parpadeo normal (cada 2–3 s), seguimiento Pan/Tilt si detecta rostro | Dispositivo vinculado y con control activo. |
| **Sin Internet** | Ícono de rayo tachado superpuesto brevemente cada pocos segundos sobre el estado actual | Caída comprobada de internet en el router LAN (bloquea funciones de voz). |

---

## ⚙️ 6. Configuración en la PWA

1. **Redes Guardadas:** Ver redes en NVS, agregar nuevas y olvidar redes.
2. **Dispositivo Vinculado:** Ver dispositivo en control, revocar o autorizar uno nuevo.
3. **Salida de Audio:** Selector de altavoz (*Dispositivo PWA* vs. *Altavoz físico de Bob*).
4. **Calidad de Video:** Presets rápidos (*Red Rápida* / *Red Lenta*).
5. **Volumen del Parlante:** Ajuste de salida de audio del ESP32.
6. **Brillo OLED:** Ajuste de contraste/brillo del SH1106 para interiores/ferias.
7. **Modo Entorno:** Presets de comportamiento (*Demo Público* vs. *Uso Normal*).
8. **Seguridad / Certificados:** Campo de actualización de `GROQ_API_KEY` y carga por red de certificados SSL.

---

## 📱 7. Notas Técnicas de Hardware y Sistema Operativo

* **Optimizaciones de Batería en MIUI / HyperOS (Redmi Note 12 Pro & Xiaomi Tab 6):**
  * Desactivar explícitamente la optimización de batería en los ajustes del sistema para el navegador (Chrome/Brave) a fin de prevenir que el SO destruya los sockets WSS/HTTPS en segundo plano.

---

## 🚀 8. Hoja de Ruta Definitiva (Fases 0 a 6)

### **Fase 0 — Fundamentos (en C++)**
1. Configuración de DuckDNS (`bobcreeper` y `bobcreeper-cam`) + generación del primer certificado con Certbot (DNS-01).
2. Estructuración del entorno C++ (Arduino sobre ESP-IDF) y verificación de RAM/mbedTLS + Servos + Audio en el mismo binario.
3. Prueba de mDNS y resolución DNS de DuckDNS hacia IP privada en casa, UATF y Robotics Creators Lab.

### **Fase 1 — Red**
4. Implementación de SoftAP + Portal Cautivo HTTP y almacenamiento NVS de redes.
5. mDNS (`bob.local`) + Renderizado de Código QR en OLED con la URL fija de DuckDNS.
6. Prueba física de escaneo del código QR a distancia de uso real.

### **Fase 2 — Seguridad y API (C++)**
7. Esquema de Token Único en LittleFS (Pairing / Revocación automática).
8. Servidor WSS con `ESPAsyncWebServer` + HTTPS en el ESP32-CAM para el stream MJPEG.
9. Chequeo de conectividad real a internet + estado degradado (icono OLED + bloqueo de voz).

### **Fase 3 — Firmware Existente, Portado a C++**
10. Portar control de Servos Pan/Tilt con zonas muertas.
11. Portar animaciones y estados del OLED (SH1106) en C++.
12. Portar la Memoria: conversión de SQLite a esquemas JSON almacenados en LittleFS.

### **Fase 4 — OTA y Certificados**
13. Sistema `ArduinoOTA` con doble partición y rollback automático.
14. Endpoint PWA para actualización remota de certificados SSL (`cert.pem` / `key.pem`) en LittleFS.

### **Fase 5 — App Web / PWA**
15. Desarrollo del esqueleto en React + Service Worker instalable.
16. Vista principal: Stream MJPEG HTTPS + Chat/Voz + Controles de movimiento WSS.
17. Visión en cliente: BlazeFace + MobileFaceNet vía ONNX Runtime Web (WASM/WebGL).
18. Pipeline de Voz: Web Audio API + Silero VAD + peticiones a Groq con la clave leída del DevKit.
19. Panel de Configuración completo (Redes, Audio, Video, Brillo OLED, Modo Entorno, Groq Key, SSL).

### **Fase 6 — Pruebas de Campo y Benchmarking**
20. Pruebas de campo multi-red reales considerando la gestión de batería MIUI.
21. Medición de FPS reales de cámara+ONNX y latencia de comandos WSS.
