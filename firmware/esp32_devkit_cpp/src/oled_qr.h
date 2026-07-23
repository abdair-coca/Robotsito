#ifndef OLED_QR_H
#define OLED_QR_H

#include <Arduino.h>
#include <U8g2lib.h>
#include <qrcode.h>

class BobOledQR {
public:
    BobOledQR();
    void begin(U8G2* display);
    void drawQRCode(const char* url, const char* label = nullptr);
    void drawEyeStatus(const char* statusStr);

private:
    U8G2* _u8g2;
};

#endif // OLED_QR_H
