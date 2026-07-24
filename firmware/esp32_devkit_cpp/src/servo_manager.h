#ifndef SERVO_MANAGER_H
#define SERVO_MANAGER_H

#include <Arduino.h>
#include <ESP32Servo.h>

class BobServoManager {
public:
    BobServoManager();
    void begin(int pinPan = 13, int pinTilt = 12);
    
    void setPanTilt(int panAngle, int tiltAngle);
    void setPan(int panAngle);
    void setTilt(int tiltAngle);
    
    void goHome();

    int getPan();
    int getTilt();

private:
    Servo _servoPan;
    Servo _servoTilt;
    
    int _currentPan;
    int _currentTilt;
    
    const int PAN_MIN = 20;
    const int PAN_MAX = 160;
    const int PAN_HOME = 90;

    const int TILT_MIN = 40;
    const int TILT_MAX = 140;
    const int TILT_HOME = 90;
};

#endif // SERVO_MANAGER_H
