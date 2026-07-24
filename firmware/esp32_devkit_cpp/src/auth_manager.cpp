#include "auth_manager.h"
#include <esp_random.h>

const char* AUTH_FILE_PATH = "/auth.json";

BobAuthManager::BobAuthManager() : _activeToken(""), _deviceName(""), _hasToken(false) {}

bool BobAuthManager::begin() {
    return loadFromFlash();
}

String BobAuthManager::generateRandomString(size_t length) {
    const char charset[] = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    String result = "";
    result.reserve(length);
    for (size_t i = 0; i < length; i++) {
        uint32_t randVal = esp_random();
        result += charset[randVal % (sizeof(charset) - 1)];
    }
    return result;
}

bool BobAuthManager::loadFromFlash() {
    if (!LittleFS.exists(AUTH_FILE_PATH)) {
        _activeToken = "";
        _deviceName = "";
        _hasToken = false;
        return false;
    }

    File file = LittleFS.open(AUTH_FILE_PATH, "r");
    if (!file) {
        Serial.println("[AuthManager] Error al abrir /auth.json");
        return false;
    }

    StaticJsonDocument<256> doc;
    DeserializationError error = deserializeJson(doc, file);
    file.close();

    if (error) {
        Serial.println("[AuthManager] Error parseando /auth.json");
        return false;
    }

    _activeToken = doc["token"] | "";
    _deviceName = doc["device_name"] | "";
    _hasToken = (_activeToken.length() > 0);

    if (_hasToken) {
        Serial.printf("[AuthManager] Token cargado de flash para dispositivo: %s\n", _deviceName.c_str());
    }
    return _hasToken;
}

bool BobAuthManager::saveToFlash() {
    File file = LittleFS.open(AUTH_FILE_PATH, "w");
    if (!file) {
        Serial.println("[AuthManager] Error al escribir /auth.json");
        return false;
    }

    StaticJsonDocument<256> doc;
    doc["token"] = _activeToken;
    doc["device_name"] = _deviceName;
    doc["updated_at"] = millis();

    if (serializeJson(doc, file) == 0) {
        Serial.println("[AuthManager] Fallo al serializar JSON en flash");
        file.close();
        return false;
    }

    file.close();
    Serial.printf("[AuthManager] Token guardado exitosamente en LittleFS para '%s'\n", _deviceName.c_str());
    return true;
}

String BobAuthManager::generateToken(const String& deviceName) {
    _deviceName = deviceName;
    _activeToken = generateRandomString(32);
    _hasToken = true;
    saveToFlash();
    return _activeToken;
}

bool BobAuthManager::validateToken(const String& token) {
    if (!_hasToken || _activeToken.length() == 0) return false;
    return _activeToken.equals(token);
}

void BobAuthManager::revokeToken() {
    _activeToken = "";
    _deviceName = "";
    _hasToken = false;
    if (LittleFS.exists(AUTH_FILE_PATH)) {
        LittleFS.remove(AUTH_FILE_PATH);
    }
    Serial.println("[AuthManager] Token revocado.");
}

bool BobAuthManager::hasActiveToken() {
    return _hasToken;
}

String BobAuthManager::getToken() {
    return _activeToken;
}

String BobAuthManager::getDeviceName() {
    return _deviceName;
}
