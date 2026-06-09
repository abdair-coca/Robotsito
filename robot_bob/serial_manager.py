"""
serial_manager.py — Único dueño de COM3 para el robot Bob.

Centraliza todos los envíos al ESP32 en un hilo dedicado con:
  - Cola priorizada: SERVO (prio 0) > ESTADO (prio 1) > SIGUIENDO (prio 2)
  - Throttle por tipo: servo ≥ 50 ms, OLED ≥ 80 ms
  - Deduplicación: mismo ESTADO no se reenvía en <1 s
  - Thread-safe: cualquier hilo puede llamar cmd_* sin lock externo
"""

import queue
import threading
import time
import serial

# ── Prioridades de cola ────────────────────────────────────────────────────────
_PRIO_SERVO    = 0
_PRIO_ESTADO   = 1
_PRIO_SIGUIENDO = 2


class SerialManager:
    def __init__(self, port: str, baud: int = 115200,
                 intervalo_servo: float = 0.05,
                 intervalo_oled: float = 0.08):
        self._port            = port
        self._baud            = baud
        self._intervalo_servo = intervalo_servo
        self._intervalo_oled  = intervalo_oled

        self._cola:  queue.PriorityQueue = queue.PriorityQueue(maxsize=60)
        self._detener = threading.Event()

        # Throttle state (solo accedido desde el hilo writer)
        self._ultimo_servo   = 0.0
        self._ultimo_oled    = 0.0
        self._ultimo_estado  = ''
        self._t_ultimo_estado = 0.0

        self._esp32: serial.Serial | None = None
        self._conectar()

        self._hilo = threading.Thread(target=self._loop, daemon=True, name='serial-writer')
        self._hilo.start()

    # ── API pública (thread-safe) ──────────────────────────────────────────────

    def cmd_servo(self, pan: float, tilt: float) -> None:
        """Enviar posición de servos. Prioridad máxima."""
        self._encolar(_PRIO_SERVO, ('servo', pan, tilt))

    def cmd_estado(self, estado: str) -> None:
        """Enviar estado OLED (ESCUCHANDO, PENSANDO, etc.)."""
        self._encolar(_PRIO_ESTADO, ('estado', estado))

    def cmd_siguiendo(self, dx: float, dy: float) -> None:
        """Enviar coordenadas de seguimiento facial al OLED."""
        self._encolar(_PRIO_SIGUIENDO, ('siguiendo', dx, dy))

    def cerrar(self) -> None:
        self._detener.set()
        self._hilo.join(timeout=1.0)
        if self._esp32 and self._esp32.is_open:
            try:
                self._esp32.close()
            except Exception:
                pass

    # ── Internos ───────────────────────────────────────────────────────────────

    def _encolar(self, prio: int, item: tuple) -> None:
        try:
            self._cola.put_nowait((prio, item))
        except queue.Full:
            pass  # descarta si la cola está llena (no bloquea nunca)

    def _conectar(self) -> None:
        try:
            esp = serial.Serial()
            esp.port          = self._port
            esp.baudrate      = self._baud
            esp.timeout       = 1
            esp.write_timeout = 0.1
            esp.dtr           = False
            esp.rts           = False
            esp.open()
            self._esp32 = esp
            print(f'[serial] Conectado en {self._port}')
        except Exception as e:
            print(f'[serial] No conecta en {self._port}: {e}')
            print('[serial] Continuando en modo sin ESP32 (solo visión)')
            self._esp32 = None

    def _enviar(self, raw: str) -> None:
        if self._esp32 is None:
            return
        try:
            self._esp32.write(raw.encode())
        except Exception:
            pass  # pérdida de frame aceptable

    def _loop(self) -> None:
        while not self._detener.is_set():
            try:
                prio, item = self._cola.get(timeout=0.1)
            except queue.Empty:
                continue

            ahora = time.monotonic()
            tipo  = item[0]

            if tipo == 'servo':
                if ahora - self._ultimo_servo < self._intervalo_servo:
                    continue  # descarta, llegará uno más nuevo
                _, pan, tilt = item
                pan_i  = int(max(0,   min(180, pan)))
                tilt_i = int(max(0,   min(180, tilt)))
                self._enviar(f'H:{pan_i},V:{tilt_i}\n')
                self._ultimo_servo = ahora

            elif tipo == 'estado':
                if ahora - self._ultimo_oled < self._intervalo_oled:
                    continue
                _, estado = item
                # No reenviar el mismo estado en menos de 1 s
                if estado == self._ultimo_estado and ahora - self._t_ultimo_estado < 1.0:
                    continue
                self._enviar(f'ESTADO:{estado}\n')
                self._ultimo_oled    = ahora
                self._ultimo_estado  = estado
                self._t_ultimo_estado = ahora

            elif tipo == 'siguiendo':
                if ahora - self._ultimo_oled < self._intervalo_oled:
                    continue
                _, dx, dy = item
                dx = max(-1.0, min(1.0, dx))
                dy = max(-1.0, min(1.0, dy))
                self._enviar(f'SIGUIENDO:{dx:.2f},{dy:.2f}\n')
                self._ultimo_oled = ahora
