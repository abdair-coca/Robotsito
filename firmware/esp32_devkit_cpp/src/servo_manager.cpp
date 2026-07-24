#include "servo_manager.h"

BobServoManager::BobServoManager() 
    : _currentPan(90), _currentTilt(90) {}

void BobServoManager::begin(int pinPan, int pinTilt) {
    ESP32PWM::allocateTimer(0);
    ESP32PWM::allocateTimer(1);
    ESP32PWM::allocateTimer(2);
    ESP32PWM::allocateTimer(3);

    _servoPan.setPeriodHertz(50);
    _servoTilt.setPeriodHertz(50);

    _servoPan.attach(pinPan, 500, 2500);
    _servoTilt.attach(pinTilt, 500, 2500);

    goHome();
    Serial.printf("[ServoManager] Servos asignados en GPIO %d (Pan) y GPIO %d (Tilt).\n", pinPan, pinTilt);
}

void BobServoManager::setPanTilt(int panAngle, int tiltAngle) {
    setPan(panAngle);
    setTilt(tiltAngle);
}

void BobServoManager::setPan(int panAngle) {
    int clamped = constrain(panAngle, PAN_MIN, PAN_MAX);
    _currentPan = clamped;
    _servoPan.write(clamped);
}

void BobServoManager::setTilt(int tiltAngle) {
    int clamped = constrain(tiltAngle, TILT_MIN, TILT_MAX);
    _currentTilt = clamped;
    _servoTilt.write(clamped);
}

void BobServoManager::goHome() {
    setPanTilt(PAN_HOME, TILT_HOME);
}

int BobServoManager::getPan() {
    return _currentPan;
}

int BobServoManager::getTilt() {
    return _currentTilt;
}
