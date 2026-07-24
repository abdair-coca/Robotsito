#ifndef AUTH_MANAGER_H
#define AUTH_MANAGER_H

#include <Arduino.h>
#include <LittleFS.h>
#include <ArduinoJson.h>

class BobAuthManager {
public:
    BobAuthManager();
    bool begin();
    
    String generateToken(const String& deviceName);
    bool validateToken(const String& token);
    void revokeToken();
    
    bool hasActiveToken();
    String getToken();
    String getDeviceName();

private:
    String _activeToken;
    String _deviceName;
    bool _hasToken;
    
    bool loadFromFlash();
    bool saveToFlash();
    String generateRandomString(size_t length);
};

#endif // AUTH_MANAGER_H
