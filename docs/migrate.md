# Plan de Migración Definitivo — Robot Bob: PWA Local-First & Dispositivo Único

Este documento define la arquitectura final, reglas de vinculación, estados visuales, configuración y la hoja de ruta de implementación fase por fase para la migración de **Robot Bob** hacia un modelo **PWA + ESP32 Local-First sin servidor**.

---

## 📐 1. Arquitectura General y Reglas de Vinculación

```
┌────────────────────────────────────────────────────────┐
│                   ESP32 (En el Robot)                 │
│  • Almacena Identidad (Memoria KV en LittleFS)        │
│  • Ejecuta Servos (Pan/Tilt), Motores y Ojos OLED     │
│  • Servidor WSS / HTTPS local + OTA con Rollback      │
│  • Token único guardado en LittleFS                    │
└──────────────────────────▲─────────────────────────────┘
                           │
               WSS (Comandos) / HTTPS (Stream) en LAN
                           │
┌──────────────────────────▼─────────────────────────────┐
│             Dispositivo Único Vinculado (PWA)          │
│  (Redmi Note 12 Pro / Xiaomi Tab 6 / Laptop / etc.)    │
│  • Visión: ONNX Runtime Web (BlazeFace + MobileFaceNet)│
│  • Voz: Web Audio API + Silero VAD + API Directa Groq  │
│  • UI: React + Service Worker (PWA Instalable)         │
└────────────────────────────────────────────────────────┘
```

### Reglas de Vinculación (Dispositivo Único)
* **Token Único Activo:** El ESP32 solo mantiene **un token activo** a la vez, guardado en LittleFS junto al nombre del dispositivo (`device_name`).
* **Revocación Automática:** Vincular un dispositivo nuevo revoca automáticamente la sesión del dispositivo anterior (mostrando una advertencia clara en la PWA entrante: *"Esto va a desvincular a [dispositivo actual]"*).
* **Reconexión Transparente:** Si la red se interrumpe temporalmente, el dispositivo vinculado se reconecta usando su token guardado en `localStorage`.

---

## 👁️ 2. Estados Visuales del OLED (SH1106)

Al limitar el control a un único dispositivo activo, el conjunto de estados del OLED se vuelve conciso, claro y de baja carga computacional:

| Estado | Expresión Visual | Cuándo Ocurre |
|---|---|---|
| **Esperando** | Ojos abiertos, relajados, parpadeo lento (cada 4–6 s) | Robot encendido, nadie vinculado. |
| **Conectando** | Ojos moviéndose de lado a lado ("buscando") | Un dispositivo está realizando el proceso de pairing. |
| **Activo** | Ojos alertas, parpadeo normal (cada 2–3 s), seguimiento Pan/Tilt si detecta rostro | Dispositivo vinculado y con control activo. |
| **Sin Internet** | Ícono de rayo tachado superpuesto brevemente cada pocos segundos sobre el estado actual | Caída comprobada de internet en el router LAN (bloquea funciones de voz). |

---

## ⚙️ 3. Configuración en la PWA

La PWA incluirá un panel de configuración centralizado para adaptar a Bob a cualquier entorno:

1. **Redes Guardadas:** Ver redes en NVS, agregar nuevas y olvidar redes.
2. **Dispositivo Vinculado:** Ver nombre del dispositivo en control, revocar o autorizar un nuevo dispositivo.
3. **Calidad de Video:** Presets rápidos (*Red Rápida* / *Red Lenta*).
4. **Volumen del Parlante:** Ajuste digital de salida de audio del ESP32.
5. **Brillo OLED:** Ajuste de contraste/brillo de la pantalla SH1106 (clave para legibilidad entre ferias con luz fuerte y habitaciones oscuras).
6. **Modo Entorno:** Presets de comportamiento (ej. *Demo Público* [sube brillo OLED, ajusta umbrales de detección facial] vs. *Uso Normal*).

---

## 📱 4. Notas Técnicas de Hardware y Sistema Operativo

* **Optimizaciones de Batería en MIUI / HyperOS (Redmi Note 12 Pro & Xiaomi Tab 6):**
  * Los dispositivos Xiaomi/Redmi cierran agresivamente conexiones en segundo plano (WebSockets/HTTPS Streams).
  * **Mitigación:** En caso de interrupciones al bloquear pantalla o cambiar de app, se debe desactivar la optimización de batería para el navegador (Chrome/Brave) directamente en los ajustes del sistema MIUI/HyperOS.

---

## 🚀 5. Hoja de Ruta Definitiva (Fase 0 a Fase 6)

### **Fase 0 — Fundamentos**
1. Configuración de DuckDNS + Certificado inicial Let's Encrypt (vía desafío DNS-01).
2. Verificación de variantes exactas del ESP32 y prueba de consumo de memoria (mbedTLS + Servos + CAM + Audio en el mismo binario).
3. Prueba de mDNS y resolución DNS a IP privada en casa, UATF y Robotics Creators Lab.

### **Fase 1 — Red**
4. Implementación de SoftAP + Portal Cautivo y almacenamiento de redes conocidas en NVS.
5. mDNS (`bob.local`) + Renderizado de Código QR en OLED con la URL fija de DuckDNS.
6. Prueba física de escaneo del código QR a distancia real.

### **Fase 2 — Seguridad y API**
7. Esquema de Token Único (Pairing / Revocación automática).
8. Servidor WSS de comandos + HTTPS separado para el stream de video MJPEG.
9. Detección periódica de conectividad a internet + modo degradado (notificación y bloqueo de voz).

### **Fase 3 — Firmware Existente, Portado**
10. Servos Pan/Tilt con límites de movimiento y zonas muertas integradas.
11. Implementación de la tabla de estados del OLED en MicroPython/C++.
12. Migración del almacenamiento de memoria (SQLite a esquemas JSON en LittleFS).

### **Fase 4 — OTA**
13. Sistema de actualización OTA con doble partición, rollback automático y disparo manual desde la PWA mediante token válido.

### **Fase 5 — App Web / PWA**
14. Desarrollo del esqueleto en React + Service Worker instalable.
15. Vista principal: Video MJPEG + Chat/Voz + Controles de movimiento.
16. Integración de visión en cliente (BlazeFace + MobileFaceNet en ONNX Runtime Web via WASM/WebGL).
17. Pipeline de Audio: Web Audio API + Silero VAD + llamadas directas a la API de Groq.
18. Implementación del panel de configuración (Redes, Vinculación, Presets de Video, Volumen, Brillo, Modo Entorno).

### **Fase 6 — Pruebas de Campo y Benchmarking**
19. Pruebas de campo multi-red reales con especial atención a MIUI en Redmi y Xiaomi Tab 6.
20. Medición empírica de FPS reales (cámara + ONNX) y latencia de comandos por WSS.
