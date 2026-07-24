#ifndef MOTOR_MANAGER_H
#define MOTOR_MANAGER_H

#include <Arduino.h>

class BobMotorManager {
public:
    BobMotorManager();
    void begin(int pinIN1 = 19, int pinIN2 = 21, int pinIN3 = 22, int pinIN4 = 23, int pinENA = 16, int pinENB = 4);
    
    void setSpeeds(int speedLeft, int speedRight); // [-100, 100]
    void stop();
    void loop(); // Verifica watchdog de 400ms

private:
    int _pinIN1, _pinIN2, _pinIN3, _pinIN4;
    int _pinENA, _pinENB;
    
    int _speedLeft;
    int _speedRight;
    
    unsigned long _lastCommandTime;
    const unsigned long WATCHDOG_TIMEOUT_MS = 400;
};

#endif // MOTOR_MANAGER_H
