#include "oled_qr.h"

BobOledQR::BobOledQR() : _u8g2(nullptr) {}

void BobOledQR::begin(U8G2* display) {
    _u8g2 = display;
}

void BobOledQR::drawQRCode(const char* url, const char* label) {
    if (!_u8g2) return;

    QRCode qrcode;
    uint8_t qrcodeData[qrcode_getBufferSize(3)];
    qrcode_initText(&qrcode, qrcodeData, 3, 0, url);

    _u8g2->clearBuffer();

    // Dibujar QR centrado a la izquierda o derecha
    int scale = 2;
    int qrSize = qrcode.size * scale;
    int offsetX = 8;
    int offsetY = (64 - qrSize) / 2;

    for (uint8_t y = 0; y < qrcode.size; y++) {
        for (uint8_t x = 0; x < qrcode.size; x++) {
            if (qrcode_getModule(&qrcode, x, y)) {
                _u8g2->drawBox(offsetX + x * scale, offsetY + y * scale, scale, scale);
            }
        }
    }

    // Texto informativo al lado del QR
    _u8g2->setFont(u8g2_font_6x10_tf);
    _u8g2->drawStr(qrSize + 16, 20, "Scan Me!");
    if (label) {
        _u8g2->drawStr(qrSize + 16, 38, label);
    }
    _u8g2->drawStr(qrSize + 16, 54, "PWA Local");

    _u8g2->sendBuffer();
}

void BobOledQR::drawEyeStatus(const char* statusStr) {
    if (!_u8g2) return;
    _u8g2->clearBuffer();
    _u8g2->setFont(u8g2_font_ncenB14_tr);
    _u8g2->drawStr(10, 35, statusStr);
    _u8g2->sendBuffer();
}
