#ifndef NET_CHECKER_H
#define NET_CHECKER_H

#include <Arduino.h>
#include <HTTPClient.h>

class BobNetChecker {
public:
    BobNetChecker();
    void loop();
    bool isConnectedToInternet();

private:
    bool _isOnline;
    unsigned long _lastCheckTime;
    const unsigned long CHECK_INTERVAL_MS = 30000; // 30 segundos
    
    void performCheck();
};

#endif // NET_CHECKER_H
