#ifndef WIFI_MANAGER_H
#define WIFI_MANAGER_H

#include <Arduino.h>
#include <WiFi.h>
#include <DNSServer.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <ArduinoJson.h>

struct WiFiNetwork {
    String ssid;
    String password;
};

class BobWiFiManager {
public:
    BobWiFiManager();
    void begin(AsyncWebServer* server, const char* duckdns_token, const char* duckdns_subdomain);
    void loop();
    
    bool isConnected();
    String getLocalIP();
    bool isSoftAP();
    
    void saveNetwork(const String& ssid, const String& password);
    void forgetNetwork(const String& ssid);
    String getSavedNetworksJson();
    
    void updateDuckDNS();

private:
    AsyncWebServer* _server;
    DNSServer _dnsServer;
    Preferences _prefs;
    
    String _duckdnsToken;
    String _duckdnsSubdomain;
    
    bool _connected;
    bool _isSoftAP;
    
    bool connectSavedNetworks();
    void startCaptivePortal();
    void setupPortalRoutes();
};

#endif // WIFI_MANAGER_H
