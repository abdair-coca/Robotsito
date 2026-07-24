/*
 * main.cpp — Robot Bob ESP32 DevKit (Firmware C++ / Arduino sobre ESP-IDF)
 *
 * Fase 4: ArduinoOTA, Monitoreo de Internet y Renovación Remota de SSL Certs en LittleFS.
 */

#include <Arduino.h>
#include <ESPAsyncWebServer.h>
#include <ESPmDNS.h>
#include <LittleFS.h>
#include <U8g2lib.h>
#include <ArduinoJson.h>

#include "wifi_manager.h"
#include "oled_qr.h"
#include "auth_manager.h"
#include "servo_manager.h"
#include "motor_manager.h"
#include "memory_manager.h"
#include "oled_eyes.h"
#include "ota_manager.h"
#include "net_checker.h"

// OLED SH1106 I2C (SCL=32, SDA=33)
U8G2_SH1106_128X64_NONAME_F_SW_I2C u8g2(U8G2_R0, /* clock=*/ 32, /* data=*/ 33, /* reset=*/ U8X8_PIN_NONE);

AsyncWebServer server(80);
AsyncWebSocket ws("/ws");

BobWiFiManager wifiMgr;
BobOledQR oledQr;
BobAuthManager authMgr;
BobServoManager servoMgr;
BobMotorManager motorMgr;
BobMemoryManager memoryMgr;
BobOledEyes oledEyes;
BobOTAManager otaMgr;
BobNetChecker netCheck;

// Token y subdominio de DuckDNS
const char* DUCKDNS_TOKEN = "8c12cd1d-1e94-48ea-b2ce-2396fac678aa";
const char* DUCKDNS_SUBDOMAIN = "bobcreeper";

void handleWebSocketMessage(void *arg, uint8_t *data, size_t len, AsyncWebSocketClient *client) {
    AwsFrameInfo *info = (AwsFrameInfo*)arg;
    if (info->final && info->index == 0 && info->len == len && info->opcode == WS_TEXT) {
        data[len] = 0;
        StaticJsonDocument<1024> doc;
        DeserializationError error = deserializeJson(doc, (char*)data);
        if (error) {
            client->text("{\"status\":\"error\",\"msg\":\"JSON invalido\"}");
            return;
        }

        String action = doc["action"] | "";

        // Acción: Vinculación / Pairing
        if (action == "pair") {
            String deviceName = doc["device_name"] | "Dispositivo Desconocido";
            
            if (authMgr.hasActiveToken()) {
                StaticJsonDocument<128> revDoc;
                revDoc["type"] = "revoked";
                revDoc["reason"] = "unlinked_by_new_device";
                revDoc["new_device"] = deviceName;
                String revJson;
                serializeJson(revDoc, revJson);
                for (auto& c : ws.getClients()) {
                    if (c.id() != client->id()) {
                        c.text(revJson);
                    }
                }
            }

            String newToken = authMgr.generateToken(deviceName);
            
            StaticJsonDocument<256> respDoc;
            respDoc["status"] = "paired";
            respDoc["token"] = newToken;
            respDoc["device_name"] = deviceName;
            String respJson;
            serializeJson(respDoc, respJson);
            client->text(respJson);

            Serial.printf("[WSS] Nuevo dispositivo vinculado: %s\n", deviceName.c_str());
            return;
        }

        // Validación de Token
        String clientToken = doc["token"] | "";
        if (!authMgr.validateToken(clientToken)) {
            client->text("{\"status\":\"unauthorized\",\"msg\":\"Token invalido o sesion revocada\"}");
            return;
        }

        // Acción: Autenticar conexión existente
        if (action == "auth") {
            client->text("{\"status\":\"authenticated\"}");
            return;
        }

        // Acción: Comandos de Control (Servos, OLED, Motores)
        if (action == "cmd") {
            String type = doc["type"] | "";
            if (type == "servo") {
                int pan = doc["pan"] | 90;
                int tilt = doc["tilt"] | 90;
                servoMgr.setPanTilt(pan, tilt);
            } else if (type == "estado") {
                String val = doc["val"] | "Esperando";
                oledEyes.setState(val);
            } else if (type == "motor") {
                int izq = doc["izq"] | 0;
                int der = doc["der"] | 0;
                motorMgr.setSpeeds(izq, der);
            }
            client->text("{\"status\":\"ok\"}");
            return;
        }

        // Acción: Gestión de Memoria (Rostros y Recuerdos)
        if (action == "memory_get_faces") {
            String facesJson = memoryMgr.getFacesJson();
            client->text("{\"status\":\"ok\",\"type\":\"faces\",\"data\":" + facesJson + "}");
            return;
        }

        if (action == "memory_save_face") {
            String name = doc["name"] | "Anonimo";
            JsonArray embedding = doc["embedding"].as<JsonArray>();
            int age = doc["age"] | 0;
            bool success = memoryMgr.saveFace(name, embedding, age);
            client->text(success ? "{\"status\":\"ok\"}" : "{\"status\":\"error\",\"msg\":\"No se pudo guardar rostro\"}");
            return;
        }

        if (action == "memory_get_history") {
            String histJson = memoryMgr.getHistoryJson();
            client->text("{\"status\":\"ok\",\"type\":\"history\",\"data\":" + histJson + "}");
            return;
        }
    }
}

void onWebSocketEvent(AsyncWebSocket *server, AsyncWebSocketClient *client, AwsEventType type, void *arg, uint8_t *data, size_t len) {
    if (type == WS_EVT_CONNECT) {
        Serial.printf("[WSS] Cliente conectado desde %s (ID: %u)\n", client->remoteIP().toString().c_str(), client->id());
    } else if (type == WS_EVT_DISCONNECT) {
        Serial.printf("[WSS] Cliente desconectado (ID: %u)\n", client->id());
    } else if (type == WS_EVT_DATA) {
        handleWebSocketMessage(arg, data, len, client);
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n[Bob DevKit C++] Iniciando Fase 4: ArduinoOTA & SSL Remote Manager...");

    // Inicializar OLED
    u8g2.begin();
    oledQr.begin(&u8g2);
    oledEyes.begin(&u8g2);
    oledQr.drawEyeStatus("Iniciando...");

    // Inicializar LittleFS
    if (!LittleFS.begin(true)) {
        Serial.println("[LittleFS] Error al montar el sistema de archivos");
    } else {
        Serial.println("[LittleFS] Montado correctamente.");
    }

    // Inicializar Módulos de Hardware y Memoria
    authMgr.begin();
    servoMgr.begin(13, 12);
    motorMgr.begin(19, 21, 22, 23, 16, 4);
    memoryMgr.begin();

    // Inicializar WiFi Manager
    wifiMgr.begin(&server, DUCKDNS_TOKEN, DUCKDNS_SUBDOMAIN);

    // Configurar mDNS (bob.local)
    if (MDNS.begin("bob")) {
        Serial.println("[mDNS] Responder activo en http://bob.local");
        MDNS.addService("http", "tcp", 80);
    }

    // Inicializar ArduinoOTA para flasheo inalámbrico
    otaMgr.begin("bob-devkit");

    // Configurar CORS
    DefaultHeaders::Instance().addHeader("Access-Control-Allow-Origin", "*");
    DefaultHeaders::Instance().addHeader("Access-Control-Allow-Headers", "*");
    DefaultHeaders::Instance().addHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");

    // Configurar WebSocket
    ws.onEvent(onWebSocketEvent);
    server.addHandler(&ws);

    // REST: Info del Robot
    server.on("/api/info", HTTP_GET, [](AsyncWebServerRequest *request) {
        Serial.println("[HTTP] Recibida peticion GET /api/info");
        StaticJsonDocument<256> doc;
        doc["status"] = "online";
        doc["robot"] = "Bob";
        doc["paired_device"] = authMgr.getDeviceName();
        doc["has_active_token"] = authMgr.hasActiveToken();
        doc["pan"] = servoMgr.getPan();
        doc["tilt"] = servoMgr.getTilt();
        doc["internet"] = netCheck.isConnectedToInternet();
        doc["free_heap"] = ESP.getFreeHeap();

        String response;
        serializeJson(doc, response);
        request->send(200, "application/json", response);
    });

    // REST: Memoria de Rostros
    server.on("/api/memory/faces", HTTP_GET, [](AsyncWebServerRequest *request) {
        String facesJson = memoryMgr.getFacesJson();
        request->send(200, "application/json", facesJson);
    });

    // REST: Control de Comandos (Respaldo por HTTP POST si WebSocket falla o esta bloqueado)
    server.on("/api/cmd", HTTP_POST, [](AsyncWebServerRequest *request) {
        // Body handler below
    }, nullptr, [](AsyncWebServerRequest *request, uint8_t *data, size_t len, size_t index, size_t total) {
        StaticJsonDocument<512> doc;
        DeserializationError err = deserializeJson(doc, data, len);
        if (err) {
            request->send(400, "application/json", "{\"status\":\"error\",\"msg\":\"JSON invalido\"}");
            return;
        }

        String type = doc["type"] | "";
        if (type == "servo") {
            int pan = doc["pan"] | 90;
            int tilt = doc["tilt"] | 90;
            servoMgr.setPanTilt(pan, tilt);
        } else if (type == "estado") {
            String val = doc["val"] | "Esperando";
            oledEyes.setState(val);
        } else if (type == "motor") {
            int izq = doc["izq"] | 0;
            int der = doc["der"] | 0;
            motorMgr.setSpeeds(izq, der);
        }

        request->send(200, "application/json", "{\"status\":\"ok\"}");
    });


    // REST: Renovación Remota de Certificados SSL (Guarda en LittleFS /cert/)
    server.on("/api/ssl/update", HTTP_POST, [](AsyncWebServerRequest *request) {
        // Manejador del cuerpo POST
    }, nullptr, [](AsyncWebServerRequest *request, uint8_t *data, size_t len, size_t index, size_t total) {
        // Parsear JSON con cert y key
        StaticJsonDocument<4096> doc;
        DeserializationError err = deserializeJson(doc, data, len);
        if (err) {
            request->send(400, "application/json", "{\"status\":\"error\",\"msg\":\"JSON invalido\"}");
            return;
        }

        String token = doc["token"] | "";
        if (!authMgr.validateToken(token)) {
            request->send(401, "application/json", "{\"status\":\"unauthorized\"}");
            return;
        }

        String cert = doc["cert"] | "";
        String key = doc["key"] | "";

        if (cert.length() > 0 && key.length() > 0) {
            if (!LittleFS.exists("/cert")) LittleFS.mkdir("/cert");
            File fCert = LittleFS.open("/cert/cert.pem", "w");
            if (fCert) { fCert.print(cert); fCert.close(); }
            File fKey = LittleFS.open("/cert/key.pem", "w");
            if (fKey) { fKey.print(key); fKey.close(); }

            Serial.println("[SSL Remote] Nuevos certificados SSL guardados en LittleFS.");
            request->send(200, "application/json", "{\"status\":\"ok\",\"msg\":\"Certificados guardados\"}");
        } else {
            request->send(400, "application/json", "{\"status\":\"error\",\"msg\":\"Cert o Key vacios\"}");
        }
    });

    // Renderizar QR en OLED o Cambiar a Modo Ojos
    if (wifiMgr.isSoftAP()) {
        oledQr.drawQRCode("http://192.168.4.1", "AP Mode");
    } else {
        String urlDuck = "https://bobcreeper.duckdns.org";
        oledQr.drawQRCode(urlDuck.c_str(), "Online");
        delay(2000);
        oledEyes.setState("Esperando");
    }

    server.begin();
    Serial.println("[Bob DevKit C++] Servidor HTTP, WSS, ArduinoOTA y SSL Remote activos.");
}

void loop() {
    wifiMgr.loop();
    motorMgr.loop(); // Watchdog de seguridad para motores
    netCheck.loop(); // Monitoreo de conectividad exterior
    oledEyes.setNoInternet(!netCheck.isConnectedToInternet());
    oledEyes.loop(); // Animación de parpadeo no bloqueante
    otaMgr.loop();   // Escuchar peticiones de flasheo inalámbrico OTA
    ws.cleanupClients();
    delay(10);
}
