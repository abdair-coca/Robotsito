/*
 * main.cpp — Robot Bob ESP32 DevKit (Firmware C++ / Arduino sobre ESP-IDF)
 *
 * Fase 2: WSS Server, AuthManager Token Único, API REST y Control.
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

// OLED SH1106 I2C (SCL=33, SDA=32)
U8G2_SH1106_128X64_NONAME_F_SW_I2C u8g2(U8G2_R0, /* clock=*/ 33, /* data=*/ 32, /* reset=*/ U8X8_PIN_NONE);

AsyncWebServer server(80);
AsyncWebSocket ws("/ws");

BobWiFiManager wifiMgr;
BobOledQR oledQr;
BobAuthManager authMgr;

// Token y subdominio de DuckDNS
const char* DUCKDNS_TOKEN = "8c12cd1d-1e94-48ea-b2ce-2396fac678aa";
const char* DUCKDNS_SUBDOMAIN = "bobcreeper";

void handleWebSocketMessage(void *arg, uint8_t *data, size_t len, AsyncWebSocketClient *client) {
    AwsFrameInfo *info = (AwsFrameInfo*)arg;
    if (info->final && info->index == 0 && info->len == len && info->opcode == WS_TEXT) {
        data[len] = 0;
        StaticJsonDocument<512> doc;
        DeserializationError error = deserializeJson(doc, (char*)data);
        if (error) {
            client->text("{\"status\":\"error\",\"msg\":\"JSON invalido\"}");
            return;
        }

        String action = doc["action"] | "";

        // Acción: Vinculación / Pairing
        if (action == "pair") {
            String deviceName = doc["device_name"] | "Dispositivo Desconocido";
            
            // Si había una sesión previa, se notifica la revocación a todos los clientes WS conectados
            if (authMgr.hasActiveToken()) {
                StaticJsonDocument<128> revDoc;
                revDoc["type"] = "revoked";
                revDoc["reason"] = "unlinked_by_new_device";
                revDoc["new_device"] = deviceName;
                String revJson;
                serializeJson(revDoc, revJson);
                ws.textAll(revJson);
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

        // Validación de Token en todas las demás acciones
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
                Serial.printf("[CMD] Servo Pan: %d, Tilt: %d\n", pan, tilt);
            } else if (type == "estado") {
                String val = doc["val"] | "FELIZ";
                Serial.printf("[CMD] OLED Estado: %s\n", val.c_str());
            } else if (type == "motor") {
                int izq = doc["izq"] | 0;
                int der = doc["der"] | 0;
                Serial.printf("[CMD] Motores Izq: %d, Der: %d\n", izq, der);
            }
            client->text("{\"status\":\"ok\"}");
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
    Serial.println("\n[Bob DevKit C++] Iniciando Fase 2: WSS Server & AuthManager...");

    // Inicializar OLED
    u8g2.begin();
    oledQr.begin(&u8g2);
    oledQr.drawEyeStatus("Iniciando...");

    // Inicializar LittleFS
    if (!LittleFS.begin(true)) {
        Serial.println("[LittleFS] Error al montar el sistema de archivos");
    } else {
        Serial.println("[LittleFS] Montado correctamente.");
    }

    // Inicializar AuthManager
    authMgr.begin();

    // Inicializar WiFi Manager
    wifiMgr.begin(&server, DUCKDNS_TOKEN, DUCKDNS_SUBDOMAIN);

    // Configurar mDNS (bob.local)
    if (MDNS.begin("bob")) {
        Serial.println("[mDNS] Responder activo en http://bob.local");
        MDNS.addService("http", "tcp", 80);
    }

    // Configurar WebSocket
    ws.onEvent(onWebSocketEvent);
    server.addHandler(&ws);

    // REST: Info del Robot
    server.on("/api/info", HTTP_GET, [](AsyncWebServerRequest *request) {
        StaticJsonDocument<256> doc;
        doc["status"] = "online";
        doc["robot"] = "Bob";
        doc["paired_device"] = authMgr.getDeviceName();
        doc["has_active_token"] = authMgr.hasActiveToken();
        doc["free_heap"] = ESP.getFreeHeap();

        String response;
        serializeJson(doc, response);
        request->send(200, "application/json", response);
    });


    // Renderizar QR en OLED
    if (wifiMgr.isSoftAP()) {
        oledQr.drawQRCode("http://192.168.4.1", "AP Mode");
    } else {
        String urlDuck = "https://bobcreeper.duckdns.org";
        oledQr.drawQRCode(urlDuck.c_str(), "Online");
    }

    server.begin();
    Serial.println("[Bob DevKit C++] Servidor HTTP y WSS activos.");
}

void loop() {
    wifiMgr.loop();
    ws.cleanupClients();
    delay(10);
}
