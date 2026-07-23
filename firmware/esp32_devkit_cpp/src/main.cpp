/*
 * main.cpp — Robot Bob ESP32 DevKit (Firmware C++ / Arduino sobre ESP-IDF)
 *
 * Fase 1: SoftAP, Captive Portal, mDNS, NVS WiFi Manager y OLED QR Code.
 */

#include <Arduino.h>
#include <ESPAsyncWebServer.h>
#include <ESPmDNS.h>
#include <LittleFS.h>
#include <U8g2lib.h>

#include "wifi_manager.h"
#include "oled_qr.h"

// OLED SH1106 I2C (SCL=33, SDA=32)
U8G2_SH1106_128X64_NONAME_F_SW_I2C u8g2(U8G2_R0, /* clock=*/ 33, /* data=*/ 32, /* reset=*/ U8X8_PIN_NONE);

AsyncWebServer server(80);
BobWiFiManager wifiMgr;
BobOledQR oledQr;

// Token y subdominio de DuckDNS
const char* DUCKDNS_TOKEN = "8c12cd1d-1e94-48ea-b2ce-2396fac678aa";
const char* DUCKDNS_SUBDOMAIN = "bobcreeper";

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n[Bob DevKit C++] Iniciando Fase 1: Red y Captive Portal...");

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

    // Inicializar WiFi Manager y Captive Portal
    wifiMgr.begin(&server, DUCKDNS_TOKEN, DUCKDNS_SUBDOMAIN);

    // Configurar mDNS (bob.local)
    if (MDNS.begin("bob")) {
        Serial.println("[mDNS] Responder activo en http://bob.local");
        MDNS.addService("http", "tcp", 80);
    }

    // Renderizar QR en OLED
    if (wifiMgr.isSoftAP()) {
        oledQr.drawQRCode("http://192.168.4.1", "AP Mode");
    } else {
        String urlDuck = "https://bobcreeper.duckdns.org";
        oledQr.drawQRCode(urlDuck.c_str(), "Online");
    }

    server.begin();
    Serial.println("[Bob DevKit C++] Servidor HTTP iniciado.");
}

void loop() {
    wifiMgr.loop();
    delay(10);
}
