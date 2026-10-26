"""
serial_simulado.py — Sustituto de SerialManager para `bob --alone` (Bob sin ESP32).

Tiene la misma API que SerialManager (cmd_servo, cmd_estado, cmd_motor, cmd_siguiendo,
cerrar), así que el resto de Bob no se entera. No abre COM3 ni WiFi: guarda lo último
que Bob le pidió, y estado_cli lo muestra en la barra del CLI:
  - posicion:       pan y tilt de la cabeza, en grados enteros (0..180).
  - _ultimo_estado: la cara del OLED (la emoción).
Los motores y la mirada (SIGUIENDO) no tienen dónde mostrarse: se descartan.
"""


class SerialSimulado:
    def __init__(self) -> None:
        self._ultimo_estado = ''
        self.posicion: tuple[int, int] | None = None

    def cmd_servo(self, pan: float, tilt: float) -> None:
        # Mismo recorte que SerialManager antes de mandar la posición al DevKit.
        self.posicion = (int(max(0, min(180, pan))), int(max(0, min(180, tilt))))

    def cmd_estado(self, estado: str) -> None:
        self._ultimo_estado = estado

    def cmd_motor(self, izq: int, der: int) -> None:
        """Sin motores: el giro no tiene dónde verse."""

    def cmd_siguiendo(self, dx: float, dy: float) -> None:
        """La mirada del OLED no se muestra en la barra."""

    def cerrar(self) -> None:
        """Nada que cerrar: no hay transporte."""
