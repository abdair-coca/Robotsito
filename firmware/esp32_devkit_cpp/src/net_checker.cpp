#include "net_checker.h"

BobNetChecker::BobNetChecker() : _isOnline(true), _lastCheckTime(0) {}

void BobNetChecker::loop() {
    unsigned long now = millis();
    if (now - _lastCheckTime > CHECK_INTERVAL_MS || _lastCheckTime == 0) {
        _lastCheckTime = now;
        performCheck();
    }
}

bool BobNetChecker::isConnectedToInternet() {
    return _isOnline;
}

void BobNetChecker::performCheck() {
    HTTPClient http;
    http.setTimeout(3000);
    http.begin("http://www.gstatic.com/generate_204");
    
    int httpCode = http.GET();
    http.end();

    bool wasOnline = _isOnline;
    _isOnline = (httpCode == 204 || httpCode == 200);

    if (wasOnline != _isOnline) {
        Serial.printf("[NetChecker] Estado de Internet cambio: %s (HTTP Code: %d)\n", 
                      _isOnline ? "CONECTADO" : "SIN INTERNET", httpCode);
    }
}
