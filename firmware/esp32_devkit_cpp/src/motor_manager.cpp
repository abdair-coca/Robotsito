#include "motor_manager.h"

BobMotorManager::BobMotorManager() 
    : _speedLeft(0), _speedRight(0), _lastCommandTime(0) {}

void BobMotorManager::begin(int pinIN1, int pinIN2, int pinIN3, int pinIN4, int pinENA, int pinENB) {
    _pinIN1 = pinIN1; _pinIN2 = pinIN2;
    _pinIN3 = pinIN3; _pinIN4 = pinIN4;
    _pinENA = pinENA; _pinENB = pinENB;

    pinMode(_pinIN1, OUTPUT);
    pinMode(_pinIN2, OUTPUT);
    pinMode(_pinIN3, OUTPUT);
    pinMode(_pinIN4, OUTPUT);

    // Configurar canales PWM para ENA (canal 0) y ENB (canal 1)
    ledcSetup(0, 1000, 8);
    ledcAttachPin(_pinENA, 0);
    ledcSetup(1, 1000, 8);
    ledcAttachPin(_pinENB, 1);

    stop();
    Serial.println("[MotorManager] Pines y PWM de motores L298N inicializados.");
}

void BobMotorManager::setSpeeds(int speedLeft, int speedRight) {
    _speedLeft = constrain(speedLeft, -100, 100);
    _speedRight = constrain(speedRight, -100, 100);
    _lastCommandTime = millis();

    // Motor Izquierdo (IN1, IN2, ENA - Canal 0)
    if (_speedLeft > 0) {
        digitalWrite(_pinIN1, HIGH);
        digitalWrite(_pinIN2, LOW);
    } else if (_speedLeft < 0) {
        digitalWrite(_pinIN1, LOW);
        digitalWrite(_pinIN2, HIGH);
    } else {
        digitalWrite(_pinIN1, LOW);
        digitalWrite(_pinIN2, LOW);
    }
    int pwmLeft = map(abs(_speedLeft), 0, 100, 0, 255);
    ledcWrite(0, pwmLeft);

    // Motor Derecho (IN3, IN4, ENB - Canal 1)
    if (_speedRight > 0) {
        digitalWrite(_pinIN3, HIGH);
        digitalWrite(_pinIN4, LOW);
    } else if (_speedRight < 0) {
        digitalWrite(_pinIN3, LOW);
        digitalWrite(_pinIN4, HIGH);
    } else {
        digitalWrite(_pinIN3, LOW);
        digitalWrite(_pinIN4, LOW);
    }
    int pwmRight = map(abs(_speedRight), 0, 100, 0, 255);
    ledcWrite(1, pwmRight);
}

void BobMotorManager::stop() {
    _speedLeft = 0;
    _speedRight = 0;
    digitalWrite(_pinIN1, LOW); digitalWrite(_pinIN2, LOW);
    digitalWrite(_pinIN3, LOW); digitalWrite(_pinIN4, LOW);
    ledcWrite(0, 0);
    ledcWrite(1, 0);
}

void BobMotorManager::loop() {
    // Watchdog: si no hay comando en 400ms y los motores se están moviendo, detenerlos
    if ((_speedLeft != 0 || _speedRight != 0) && (millis() - _lastCommandTime > WATCHDOG_TIMEOUT_MS)) {
        stop();
        Serial.println("[MotorManager] Watchdog activado: Motores detenidos por timeout.");
    }
}
