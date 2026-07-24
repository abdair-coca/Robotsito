#include "ota_manager.h"

BobOTAManager::BobOTAManager() {}

void BobOTAManager::begin(const char* hostname) {
    ArduinoOTA.setHostname(hostname);
    
    ArduinoOTA.onStart([]() {
        String type;
        if (ArduinoOTA.getCommand() == U_FLASH) {
            type = "sketch/firmware";
        } else { // U_SPIFFS / U_LITTLEFS
            type = "filesystem";
        }
        Serial.println("[OTA] Inicio de actualización inalámbrica: " + type);
    });

    ArduinoOTA.onEnd([]() {
        Serial.println("\n[OTA] Actualización finalizada con éxito. Reiniciando...");
    });

    ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
        Serial.printf("[OTA] Progreso: %u%%\r", (progress / (total / 100)));
    });

    ArduinoOTA.onError([](ota_error_t error) {
        Serial.printf("[OTA] Error[%u]: ", error);
        if (error == OTA_AUTH_ERROR) Serial.println("Fallo de autenticación");
        else if (error == OTA_BEGIN_ERROR) Serial.println("Fallo en Begin");
        else if (error == OTA_CONNECT_ERROR) Serial.println("Fallo en Conexión");
        else if (error == OTA_RECEIVE_ERROR) Serial.println("Fallo en Recepción");
        else if (error == OTA_END_ERROR) Serial.println("Fallo en End");
    });

    ArduinoOTA.begin();
    Serial.printf("[OTA] Servicio ArduinoOTA iniciado en hostname: %s\n", hostname);
}

void BobOTAManager::loop() {
    ArduinoOTA.handle();
}
