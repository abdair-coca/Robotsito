#ifndef MEMORY_MANAGER_H
#define MEMORY_MANAGER_H

#include <Arduino.h>
#include <LittleFS.h>
#include <ArduinoJson.h>

class BobMemoryManager {
public:
    BobMemoryManager();
    bool begin();
    
    // Gestión de Rostros Conocidos
    String getFacesJson();
    bool saveFace(const String& name, const JsonArray& embedding512, int age = 0);
    bool deleteFace(const String& id);
    
    // Gestión de Historial / Recuerdos
    String getHistoryJson();
    bool addMemory(const String& topic, const String& details);
    
private:
    const char* FACES_FILE = "/memory/faces.json";
    const char* HISTORY_FILE = "/memory/history.json";
    
    void ensureMemoryDirectory();
};

#endif // MEMORY_MANAGER_H
