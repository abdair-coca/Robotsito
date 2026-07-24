#ifndef OLED_EYES_H
#define OLED_EYES_H

#include <Arduino.h>
#include <U8g2lib.h>

class BobOledEyes {
public:
    BobOledEyes();
    void begin(U8G2* display);
    
    void setState(const String& state);
    void setNoInternet(bool noInternet);
    
    void loop(); // Maneja animaciones y parpadeos no bloqueantes

private:
    U8G2* _u8g2;
    String _currentState;
    bool _noInternet;
    
    unsigned long _lastBlinkTime;
    unsigned long _blinkDurationMs;
    bool _isBlinking;
    
    int _eyePupilX;
    int _eyePupilY;

    void renderEyes();
    void drawNoInternetIcon();
};

#endif // OLED_EYES_H
