#ifndef OTA_MANAGER_H
#define OTA_MANAGER_H

#include <Arduino.h>
#include <ArduinoOTA.h>

class BobOTAManager {
public:
    BobOTAManager();
    void begin(const char* hostname = "bob-devkit");
    void loop();
};

#endif // OTA_MANAGER_H
