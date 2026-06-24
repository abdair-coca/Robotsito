# Locomoción — motores DC

Que Bob **se desplace** (no solo mueva la cabeza pan/tilt). Tracción diferencial
(tipo tanque) con **2 motores DC** vía puente H **L298N**. Reusa el canal de control
de texto (USB COM3 / TCP 5007) y el `SerialManager` como único dueño.

> **Estado:** Fases 0-3 ✅ validadas en vivo. Pendiente: traslación adelante/atrás
> autónoma (requiere sensores de obstáculo/borde que no hay aún).

---

## Cableado (dado por el usuario)

| Señal | Pin ESP32 | Driver |
|---|---|---|
| IN1 | GPIO19 | dirección motor izquierdo (A) |
| IN2 | GPIO21 | dirección motor izquierdo (A) |
| IN3 | GPIO22 | dirección motor derecho (B) |
| IN4 | GPIO23 | dirección motor derecho (B) |
| ENA | GPIO16 | PWM velocidad izquierdo |
| ENB | GPIO4 | PWM velocidad derecho |
| GND | GND | tierra común |

- Motor izquierdo → OUT1/OUT2 (IN1/IN2); derecho → OUT3/OUT4 (IN3/IN4).
- Tabla por motor: `IN_a=1,IN_b=0`→adelante · `0,1`→atrás · `0,0`→libre · `1,1`→freno.

---

## Estado por fase

- **Fase 0 (bloqueante, resuelto):** OLED movido a I2C 32/33 (libera 21/22 para motores);
  motores con cargador aparte + GND común.
- **Fase 1 (firmware) ✅:** `mover_motores()`, comando `M:<izq>,<der>`, watchdog 400 ms.
- **Fase 2 (laptop) ✅:** `SerialManager.cmd_motor()` + `tests/test_motor.py` (teclado WASD,
  reenvía cada 150 ms).
- **Fase 3 (autónomo) ✅:** `behavior._maybe_girar_cuerpo` — Bob **gira sobre su eje** (no
  traslada) en ráfagas cortas (`GIRO_BURST_S`) + cooldown cuando el pan se satura y la
  cara sigue corrida. Config: `MOTORES_ENABLED`, `GIRO_*`, `GIRO_INVERTIR`.
- **Velocidad ✅:** ENA=GPIO16, ENB=GPIO4 por PWM; `M:` lleva velocidad con signo
  [-100,100]; `GIRO_VELOCIDAD` en config.
- **Pendiente:** traslación adelante/atrás autónoma — solo con sensores de obstáculo/borde.

---

## Software (patrón existente)

NO abrir los pines desde la laptop — todo va por el firmware.
1. **Firmware `firmware/Esp32/main.py`:** pines `Pin.OUT`, `mover_motores(izq,der)` por la
   tabla de verdad, comando `M:<izq>,<der>` en `aplicar_cmd`, **watchdog** que para si no
   llega comando en ~400 ms (evita que Bob se escape si se corta la conexión), parar al
   boot y en cualquier error.
2. **`robot_bob/serial_manager.py`:** `cmd_motor(izq,der)` con prioridad alta en la cola +
   throttle. Mismo patrón que `cmd_servo`/`cmd_estado`.
3. **Control en `behavior.py`:** decide cuándo girar (cara lejana, deambular en IDLE).
4. **`config.py`:** `MOTORES_ENABLED`, velocidades, watchdog timeout.

---

## Seguridad (no opcional)

- Watchdog que para si no hay comandos.
- Parar motores en el `finally` de `main.py` y al cerrar `SerialManager`/al dormir.
- Probar SIEMPRE primero con las ruedas levantadas del piso.
- Límite de velocidad/tiempo para que no se aleje de la laptop (control por WiFi/USB).

> ⚠️ Arrastra el riesgo de **brownout** (ver [HARDWARE.md](HARDWARE.md) §4): los motores
> tiran mucha corriente. Fuente separada para motores = condición para que sea viable.
