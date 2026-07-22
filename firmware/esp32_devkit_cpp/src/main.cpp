/*
 * main.cpp — Robot Bob ESP32 DevKit (Firmware C++ / Arduino sobre ESP-IDF)
 *
 * Fase 0: Verificación de RAM budget, mbedTLS, WiFi, WSS y Heap inicial.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ESPAsyncWebServer.h>
#include <LittleFS.h>
#include <ArduinoJson.h>

// Definición de Servidor Web Asíncrono en puerto 8080 (WSS) y 80 (HTTP)
AsyncWebServer server(80);
AsyncWebSocket ws("/ws");

void printMemoryStatus() {
    Serial.println("=========================================");
    Serial.printf(" Free Heap: %u bytes\n", ESP.getFreeHeap());
    Serial.printf(" Min Free Heap: %u bytes\n", ESP.getMinFreeHeap());
    Serial.printf(" Max Alloc Heap: %u bytes\n", ESP.getMaxAllocHeap());
    if (psramFound()) {
        Serial.printf(" Free PSRAM: %u bytes\n", ESP.getFreePsram());
    } else {
        Serial.println(" PSRAM: No detectada");
    }
    Serial.println("=========================================");
}

void onWebSocketEvent(AsyncWebSocket *server, AsyncWebSocketClient *client, AwsEventType type, void *arg, uint8_t *data, size_t len) {
    if (type == WS_EVT_CONNECT) {
        Serial.printf("[WSS] Cliente #%u conectado desde %s\n", client->id(), client->remoteIP().toString().c_str());
        printMemoryStatus();
    } else if (type == WS_EVT_DISCONNECT) {
        Serial.printf("[WSS] Cliente #%u desconectado\n", client->id());
    } else if (type == WS_EVT_DATA) {
        // Manejo de mensajes de control
        Serial.printf("[WSS] Datos recibidos: %.*s\n", (int)len, (char*)data);
    }
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n[Bob DevKit C++] Iniciando Fase 0: Verificación de Sistema...");

    printMemoryStatus();

    // Inicializar LittleFS
    if (!LittleFS.begin(true)) {
        Serial.println("[LittleFS] Error al montar el sistema de archivos");
    } else {
        Serial.printf("[LittleFS] Montado correctamente. Total: %u KB, Usado: %u KB\n",
                      LittleFS.totalBytes() / 1024, LittleFS.usedBytes() / 1024);
    }

    // Configuración WebSocket
    ws.onEvent(onWebSocketEvent);
    server.addHandler(&ws);

    server.on("/api/info", HTTP_GET, [](AsyncWebServerRequest *request) {
        StaticJsonDocument<256> doc;
        doc["status"] = "online";
        doc["robot"] = "Bob";
        doc["free_heap"] = ESP.getFreeHeap();
        doc["min_free_heap"] = ESP.getMinFreeHeap();

        String response;
        serializeJson(doc, response);
        request->send(200, "application/json", response);
    });

    Serial.println("[Bob DevKit C++] Sistema listo para conexión WiFi.");
}

void loop() {
    ws.cleanupClients();
    delay(10);
}
