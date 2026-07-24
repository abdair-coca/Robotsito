#include "oled_eyes.h"

BobOledEyes::BobOledEyes() 
    : _u8g2(nullptr), _currentState("Esperando"), _noInternet(false),
      _lastBlinkTime(0), _blinkDurationMs(200), _isBlinking(false),
      _eyePupilX(0), _eyePupilY(0) {}

void BobOledEyes::begin(U8G2* display) {
    _u8g2 = display;
    _lastBlinkTime = millis();
}

void BobOledEyes::setState(const String& state) {
    if (_currentState != state) {
        _currentState = state;
        renderEyes();
    }
}

void BobOledEyes::setNoInternet(bool noInternet) {
    if (_noInternet != noInternet) {
        _noInternet = noInternet;
        renderEyes();
    }
}

void BobOledEyes::loop() {
    if (!_u8g2) return;
    
    unsigned long now = millis();

    // Parpadeo aleatorio cada 3 a 5 segundos
    if (!_isBlinking && (now - _lastBlinkTime > (unsigned long)random(3000, 5000))) {
        _isBlinking = true;
        _lastBlinkTime = now;
        renderEyes();
    } else if (_isBlinking && (now - _lastBlinkTime > _blinkDurationMs)) {
        _isBlinking = false;
        _lastBlinkTime = now;
        renderEyes();
    }
}

void BobOledEyes::renderEyes() {
    if (!_u8g2) return;

    _u8g2->clearBuffer();

    if (_isBlinking) {
        // Ojos cerrados (línea horizontal)
        _u8g2->drawHLine(24, 32, 30);
        _u8g2->drawHLine(74, 32, 30);
    } else if (_currentState == "FELIZ") {
        // Ojos arqueados tipo feliz ^ ^
        _u8g2->drawCircle(39, 36, 15, U8G2_DRAW_UPPER_RIGHT | U8G2_DRAW_UPPER_LEFT);
        _u8g2->drawCircle(89, 36, 15, U8G2_DRAW_UPPER_RIGHT | U8G2_DRAW_UPPER_LEFT);
    } else if (_currentState == "SORPRENDIDO") {
        // Ojos grandes abiertos O O
        _u8g2->drawDisc(39, 32, 18, U8G2_DRAW_ALL);
        _u8g2->drawDisc(89, 32, 18, U8G2_DRAW_ALL);
    } else if (_currentState == "TRISTE") {
        // Ojos tristes / bajos
        _u8g2->drawCircle(39, 28, 14, U8G2_DRAW_LOWER_RIGHT | U8G2_DRAW_LOWER_LEFT);
        _u8g2->drawCircle(89, 28, 14, U8G2_DRAW_LOWER_RIGHT | U8G2_DRAW_LOWER_LEFT);
    } else if (_currentState == "Conectando") {
        // Ojos mirando a los lados (animación)
        int offsetX = (millis() / 300) % 2 == 0 ? -6 : 6;
        _u8g2->drawRBox(24 + offsetX, 16, 28, 32, 6);
        _u8g2->drawRBox(74 + offsetX, 16, 28, 32, 6);
    } else {
        // Estado por defecto: "Esperando", "Activo" u otros
        _u8g2->drawRBox(24, 16, 28, 32, 6);
        _u8g2->drawRBox(74, 16, 28, 32, 6);
    }

    // Superponer icono de Sin Internet si aplica
    if (_noInternet) {
        drawNoInternetIcon();
    }

    _u8g2->sendBuffer();
}

void BobOledEyes::drawNoInternetIcon() {
    // Dibujar pequeño icono de rayo tachado o 'X' en la esquina superior derecha (110, 2)
    _u8g2->setFont(u8g2_font_open_iconic_embedded_1x_t);
    _u8g2->drawGlyph(115, 10, 74); // Icono de advertencia/desconexión
}
