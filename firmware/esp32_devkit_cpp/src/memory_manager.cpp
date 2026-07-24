#include "memory_manager.h"

BobMemoryManager::BobMemoryManager() {}

bool BobMemoryManager::begin() {
    ensureMemoryDirectory();
    return LittleFS.exists(FACES_FILE);
}

void BobMemoryManager::ensureMemoryDirectory() {
    if (!LittleFS.exists("/memory")) {
        LittleFS.mkdir("/memory");
    }
    if (!LittleFS.exists(FACES_FILE)) {
        File file = LittleFS.open(FACES_FILE, "w");
        if (file) {
            file.print("[]");
            file.close();
        }
    }
    if (!LittleFS.exists(HISTORY_FILE)) {
        File file = LittleFS.open(HISTORY_FILE, "w");
        if (file) {
            file.print("[]");
            file.close();
        }
    }
}

String BobMemoryManager::getFacesJson() {
    if (!LittleFS.exists(FACES_FILE)) return "[]";
    File file = LittleFS.open(FACES_FILE, "r");
    if (!file) return "[]";
    String json = file.readString();
    file.close();
    return json;
}

bool BobMemoryManager::saveFace(const String& name, const JsonArray& embedding512, int age) {
    ensureMemoryDirectory();
    String currentJson = getFacesJson();
    
    DynamicJsonDocument doc(16384);
    deserializeJson(doc, currentJson);
    JsonArray array = doc.as<JsonArray>();

    JsonObject newFace = array.createNestedObject();
    newFace["id"] = String(millis());
    newFace["name"] = name;
    newFace["age"] = age;
    newFace["created_at"] = millis();
    newFace["embedding"] = embedding512;

    File file = LittleFS.open(FACES_FILE, "w");
    if (!file) return false;
    serializeJson(doc, file);
    file.close();
    Serial.printf("[MemoryManager] Rostro '%s' guardado en memoria LittleFS.\n", name.c_str());
    return true;
}

bool BobMemoryManager::deleteFace(const String& id) {
    ensureMemoryDirectory();
    String currentJson = getFacesJson();
    
    DynamicJsonDocument doc(16384);
    deserializeJson(doc, currentJson);
    JsonArray array = doc.as<JsonArray>();

    DynamicJsonDocument newDoc(16384);
    JsonArray newArray = newDoc.to<JsonArray>();

    for (JsonObject f : array) {
        if (f["id"].as<String>() != id) {
            newArray.add(f);
        }
    }

    File file = LittleFS.open(FACES_FILE, "w");
    if (!file) return false;
    serializeJson(newDoc, file);
    file.close();
    Serial.printf("[MemoryManager] Rostro ID '%s' eliminado de LittleFS.\n", id.c_str());
    return true;
}

String BobMemoryManager::getHistoryJson() {
    if (!LittleFS.exists(HISTORY_FILE)) return "[]";
    File file = LittleFS.open(HISTORY_FILE, "r");
    if (!file) return "[]";
    String json = file.readString();
    file.close();
    return json;
}

bool BobMemoryManager::addMemory(const String& topic, const String& details) {
    ensureMemoryDirectory();
    String currentJson = getHistoryJson();
    
    DynamicJsonDocument doc(8192);
    deserializeJson(doc, currentJson);
    JsonArray array = doc.as<JsonArray>();

    JsonObject newMem = array.createNestedObject();
    newMem["timestamp"] = millis();
    newMem["topic"] = topic;
    newMem["details"] = details;

    File file = LittleFS.open(HISTORY_FILE, "w");
    if (!file) return false;
    serializeJson(doc, file);
    file.close();
    Serial.printf("[MemoryManager] Recuerdo guardado: '%s'\n", topic.c_str());
    return true;
}
