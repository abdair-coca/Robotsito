"""
serial_manager.py — Único dueño del canal de control del DevKit (servos + OLED).

Centraliza todos los envíos al ESP32 en un hilo dedicado con:
  - Cola priorizada: SERVO (prio 0) > ESTADO (prio 1) > SIGUIENDO (prio 2)
  - Throttle por tipo: servo ≥ 50 ms, OLED ≥ 80 ms
  - Deduplicación: mismo ESTADO no se reenvía en <1 s
  - Thread-safe: cualquier hilo puede llamar cmd_* sin lock externo

Transporte (resuelto en _conectar, en orden):
  1. WiFi/TCP al DevKit (si USE_WIFI_SERIAL) — manda las MISMAS líneas de texto
     que el viejo serial, ahora por socket. Ver firmware Esp32/main.py.
  2. USB COM3 (pyserial) — fallback.
  3. Sin ESP32 — modo solo visión (no rompe la demo).

El protocolo de texto es idéntico en ambos transportes:
  H:<pan>,V:<tilt>\\n  |  ESTADO:<NOMBRE>\\n  |  SIGUIENDO:<dx>,<dy>\\n
"""

import queue
import socket
import threading
import time
import serial

try:
    from config import USE_WIFI_SERIAL, CONTROL_IP, CONTROL_PORT
except Exception:               # config viejo sin estas claves → solo USB
    USE_WIFI_SERIAL = False
    CONTROL_IP      = ""
    CONTROL_PORT    = 0

# ── Prioridades de cola ────────────────────────────────────────────────────────
_PRIO_SERVO    = 0
_PRIO_ESTADO   = 1
_PRIO_SIGUIENDO = 2


class SerialManager:
    def __init__(self, port: str, baud: int = 115200,
                 intervalo_servo: float = 0.05,
                 intervalo_oled: float = 0.08,
                 use_wifi: bool | None = None,
                 control_ip: str | None = None,
                 control_port: int | None = None):
        self._port            = port
        self._baud            = baud
        self._intervalo_servo = intervalo_servo
        self._intervalo_oled  = intervalo_oled

        # Config WiFi (None = tomar de config.py)
        self._use_wifi     = USE_WIFI_SERIAL if use_wifi is None else use_wifi
        self._control_ip   = CONTROL_IP   if control_ip   is None else control_ip
        self._control_port = CONTROL_PORT if control_port is None else control_port

        self._cola:  queue.PriorityQueue = queue.PriorityQueue(maxsize=60)
        self._detener = threading.Event()

        # Reconexión: si el DevKit se reinicia (brownout) el socket WiFi queda
        # muerto. El writer reintenta reconectar cada _intervalo_reconexion s.
        self._intervalo_reconexion = 2.0
        self._t_reintento          = 0.0

        # Throttle state (solo accedido desde el hilo writer)
        self._ultimo_servo   = 0.0
        self._ultimo_oled    = 0.0
        self._ultimo_estado  = ''
        self._t_ultimo_estado = 0.0

        # Transportes (solo uno activo)
        self._modo: str = 'none'                       # 'wifi' | 'usb' | 'none'
        self._esp32: serial.Serial | None = None
        self._sock:  socket.socket  | None = None
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
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
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
        """Resuelve el transporte: WiFi → USB → sin ESP32."""
        if self._use_wifi and self._control_ip and self._control_port:
            if self._conectar_wifi():
                return
            print('[serial] WiFi no disponible, intentando USB...')
        self._conectar_usb()

    def _conectar_wifi(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3.0)
            s.connect((self._control_ip, self._control_port))
            s.settimeout(None)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock = s
            self._modo = 'wifi'
            print(f'[serial] Conectado WiFi {self._control_ip}:{self._control_port}')
            return True
        except Exception as e:
            print(f'[serial] No conecta WiFi {self._control_ip}:{self._control_port}: {e}')
            self._sock = None
            return False

    def _conectar_usb(self) -> None:
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
            self._modo  = 'usb'
            print(f'[serial] Conectado en {self._port}')
        except Exception as e:
            print(f'[serial] No conecta en {self._port}: {e}')
            print('[serial] Continuando en modo sin ESP32 (solo visión)')
            self._esp32 = None
            self._modo  = 'none'

    def _enviar(self, raw: str) -> None:
        try:
            if self._modo == 'wifi' and self._sock is not None:
                self._sock.sendall(raw.encode())
            elif self._modo == 'usb' and self._esp32 is not None:
                self._esp32.write(raw.encode())
        except Exception:
            # El transporte se cayó (p.ej. el DevKit hizo brownout y reinició).
            # Marcamos caído; el loop reconectará. Este frame se pierde (ok).
            self._marcar_caido()

    def _marcar_caido(self) -> None:
        """Cierra el transporte muerto y deja _modo='none' para forzar reconexión."""
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._esp32 is not None:
            try:
                self._esp32.close()
            except Exception:
                pass
            self._esp32 = None
        if self._modo != 'none':
            print('[serial] Transporte caído (¿reinicio del DevKit?). Reconectando...')
        self._modo = 'none'

    def _reconectar(self) -> None:
        """Reintenta resolver el transporte (WiFi → USB → none) y re-anuncia estado."""
        self._conectar()
        if self._modo != 'none':
            # Reenviar el último estado para que el OLED quede coherente tras el reset.
            est = self._ultimo_estado or 'ESPERANDO'
            self._ultimo_estado = ''     # forzar reenvío (saltar la deduplicación)
            self._enviar(f'ESTADO:{est}\n')

    def _loop(self) -> None:
        while not self._detener.is_set():
            # Si el transporte está caído, reintentar reconexión (throttleado).
            if self._modo == 'none':
                ahora = time.monotonic()
                if ahora - self._t_reintento >= self._intervalo_reconexion:
                    self._t_reintento = ahora
                    self._reconectar()

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
