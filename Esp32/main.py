# main.py — ESP32 DevKit COMPLETO
# Integra: Audio streaming half-duplex (con barge-in soportado), servos pan/tilt,
# OLED de ojos, y comandos serial desde la laptop.
#
# Hilo 1 (_thread): hilo_audio  — state machine LISTEN ↔ PLAY a 8 kHz
# Hilo 2 (_thread): hilo_oled   — renderiza el estado emocional a ~12 fps
# Loop principal:                comandos Serial (H:XX,V:XX | ESTADO:XX | SIGUIENDO:dx,dy)
#
# Protocolo de audio (debe coincidir con scripts/VoiceChat/audio_io.py):
#   PORT_MIC (ESP32 -> laptop): stream uint8 8 kHz crudo, sin headers.
#   PORT_SPK (laptop -> ESP32): | 4 bytes BE length | N bytes |
#       length > 0  normal  -> audio uint8 a reproducir
#       length == 0xFFFFFFFE -> KEEPALIVE (no-op)
#       length == 0xFFFFFFFF -> STOP (descartado en half-duplex)

from machine import ADC, DAC, Pin, PWM
import network, usocket, utime, struct, gc, sys, select
import _thread
from config import SSID, PASSWORD
# IP estática opcional. Si el config del dispositivo define STATIC_IP, el ESP32
# fija SIEMPRE esa IP (ej. 192.168.0.23) en vez de depender de DHCP — así la
# laptop (robot_bob/config.py CONTROL_IP) siempre la encuentra. Si no está
# definida, se cae a DHCP (comportamiento viejo).
try:
    from config import STATIC_IP, GATEWAY, SUBNET, DNS
except Exception:
    STATIC_IP = GATEWAY = SUBNET = DNS = None

# OLED — opcional: si el módulo no está, se omite el hilo OLED.
# Usamos el nuevo API tick() / do_blink() / do_wink() del módulo.
try:
    import oled_ojos
    OLED_DISPONIBLE = True
except Exception as _e_oled:
    OLED_DISPONIBLE = False
    print('OLED no disponible:', _e_oled)

import math
import urandom


# ══════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════

PORT_MIC    = 5005
PORT_SPK    = 5006
PORT_CTRL   = 5007       # comandos servos/estado/seguimiento por WiFi
                         # DEBE coincidir con CONTROL_PORT en robot_bob/config.py
SAMPLE_RATE = 8000
INTERVAL_US = 1_000_000 // SAMPLE_RATE   # 166.67 µs por muestra

MIC_CHUNK_SIZE  = 256                    # bytes por paquete TCP de mic (32 ms)
SPK_RECV_SIZE   = 1024                   # bytes pedidos por refill del DAC
SPK_CHECK_EVERY = 32                     # samples entre peeks del socket spk

EAGAIN = 11

# Límites de los servos
PAN_MIN,  PAN_MAX  =   0, 180
TILT_MIN, TILT_MAX =  40, 140


# ══════════════════════════════════════════════════════════════════
# HARDWARE
# ══════════════════════════════════════════════════════════════════

# Micrófono MAX9814 sobre ADC1 (GPIO34)
adc = ADC(Pin(34))
adc.atten(ADC.ATTN_11DB)
adc.width(ADC.WIDTH_12BIT)

# Speaker (PAM8403) por el DAC interno (GPIO25)
dac = DAC(Pin(25))
dac.write(128)

# Servos pan/tilt
pan  = PWM(Pin(13), freq=50)
tilt = PWM(Pin(12), freq=50)

# Cachés: acceso a un local es ~3x más rápido que resolver el atributo
_adc_read   = adc.read
_dac_write  = dac.write
_ticks_us   = utime.ticks_us
_ticks_diff = utime.ticks_diff


# ══════════════════════════════════════════════════════════════════
# ESTADO GLOBAL (compartido entre hilos)
# ══════════════════════════════════════════════════════════════════
# Las escrituras simples de int/float/string son atómicas en MicroPython,
# así que no necesitamos locks. Lo único que puede pelearse es estado_robot:
# el hilo de audio lo pone en ESCUCHANDO/HABLANDO/ESPERANDO según su modo,
# y la laptop puede sobrescribirlo por serial (ESTADO:FELIZ, etc.).

estado_robot = 'ESPERANDO'   # ESPERANDO | ESCUCHANDO | PENSANDO | HABLANDO
                              # | FELIZ | CURIOSO | SIGUIENDO
sig_dx       = 0.0            # -1..1 — coord X del rostro (seguimiento)
sig_dy       = 0.0            # -1..1
frame_habla  = 0              # contador para animar la boca/ojos al hablar
reproduciendo = False         # True mientras el DAC reproduce audio


# ══════════════════════════════════════════════════════════════════
# SERVOS
# ══════════════════════════════════════════════════════════════════

def angulo_duty(grados):
    """Grados (0-180) -> duty cycle PWM."""
    pulso = 0.5 + (grados / 180.0) * 2.0    # 0.5–2.5 ms
    return int((pulso / 20.0) * 1023)

def mover_servos(pan_g, tilt_g):
    pan_g  = max(PAN_MIN,  min(PAN_MAX,  pan_g))
    tilt_g = max(TILT_MIN, min(TILT_MAX, tilt_g))
    pan.duty(angulo_duty(pan_g))
    tilt.duty(angulo_duty(tilt_g))

def parsear_servo(cmd):
    """Parsea 'H:90,V:45'. Devuelve (h, v) o (None, None)."""
    try:
        partes = cmd.strip().split(',')
        h = int(partes[0].split(':')[1])
        v = int(partes[1].split(':')[1])
        return h, v
    except Exception:
        return None, None

# Inicializar al centro
mover_servos(90, 90)
print('Servos inicializados en centro (90, 90)')


# ══════════════════════════════════════════════════════════════════
# WIFI
# ══════════════════════════════════════════════════════════════════

def conectar_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    # Desactivar power-save del WiFi: corriente más estable (menos picos del
    # radio que sagan el riel y hacen temblar al servo) + menor latencia de
    # control. Si el firmware no soporta la constante, se ignora.
    try:
        wlan.config(pm=network.WLAN.PM_NONE)
    except Exception:
        pass
    # Bajar potencia de TX: en setup de corto alcance reduce el pico de
    # corriente del radio (ayuda al brownout) y el desense del ESP32-CAM que
    # está al lado. ~13 dBm es de sobra para metros de distancia.
    try:
        wlan.config(txpower=13)
    except Exception:
        pass
    # Fijar IP estática ANTES de conectar (si el config la define).
    if STATIC_IP:
        try:
            wlan.ifconfig((STATIC_IP,
                           SUBNET  or '255.255.255.0',
                           GATEWAY or '192.168.0.1',
                           DNS     or '8.8.8.8'))
            print('IP estática fijada:', STATIC_IP)
        except Exception as e:
            print('No se pudo fijar IP estática, usando DHCP:', e)
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print('Ya conectado. IP:', ip)
        return ip
    print('Conectando al WiFi...')
    wlan.connect(SSID, PASSWORD)
    for _ in range(30):
        if wlan.isconnected():
            ip = wlan.ifconfig()[0]
            print('Conectado! IP del ESP32:', ip)
            return ip
        utime.sleep(0.5)
        print('.', end='')
    raise RuntimeError('No se pudo conectar al WiFi')


# ══════════════════════════════════════════════════════════════════
# AUDIO — state machine LISTEN ↔ PLAY (busy-wait determinista)
# ══════════════════════════════════════════════════════════════════
# Este firmware NO usa Timer ISR (los callbacks de MicroPython no sostienen
# 8 kHz). En su lugar, cada muestra se garantiza con busy-wait sobre ticks_us.

def listen_mode(conn_mic, conn_spk, mic_buf, header_buf, header_state):
    """
    LISTEN con timing ABSOLUTO.

    El reloj objetivo del sample N es `start + N*125µs`. Si una preempción
    de FreeRTOS / WiFi atrasa nuestro busy-wait, los samples siguientes
    se disparan SIN espera hasta alcanzar el reloj real → el ADC mantiene
    la tasa media de 8000 Hz aunque haya jitter.
    Cada ~12 s re-anclamos `start` para que sample_idx*INTERVAL_US no se
    desborde el rango de ticks_us.
    """
    global estado_robot
    estado_robot = 'ESCUCHANDO'

    pos = 0
    chk = 0
    start = _ticks_us()
    sample_idx = 0

    while True:
        # ── 1) sample mic ────────────────────────────────────────
        mic_buf[pos] = _adc_read() >> 4
        pos += 1
        if pos >= MIC_CHUNK_SIZE:
            try:
                conn_mic.send(mic_buf)
            except OSError as e:
                if not (e.args and e.args[0] == EAGAIN):
                    raise e
            pos = 0

        # ── 2) peek del socket spk para ver si llegó audio ───────
        chk += 1
        if chk >= SPK_CHECK_EVERY:
            chk = 0
            try:
                hp = header_state[0]
                got = conn_spk.recv(4 - hp)
                if got:
                    n = len(got)
                    for i in range(n):
                        header_buf[hp + i] = got[i]
                    hp += n
                    header_state[0] = hp
                    if hp >= 4:
                        tam = struct.unpack('>I', header_buf)[0]
                        header_state[0] = 0
                        if tam > 0 and tam != 0xFFFFFFFF and tam != 0xFFFFFFFE:
                            return tam
            except OSError as e:
                if not (e.args and e.args[0] == EAGAIN):
                    raise e

        # ── 3) busy-wait absoluto: target = start + idx*125µs ────
        sample_idx += 1
        target_us = sample_idx * INTERVAL_US
        while _ticks_diff(_ticks_us(), start) < target_us:
            pass

        # ── 4) re-anclar cada ~12 s para no overflow ticks_us ────
        if sample_idx >= 100000:
            start = _ticks_us()
            sample_idx = 0


def play_mode(conn_spk, total, recv_buf):
    """
    PLAY con timing ABSOLUTO.

    Cada sample N debe escribirse al DAC en el tick `start + N*125µs`.
    Si la lectura del socket o un context-switch nos atrasan, los
    siguientes samples se disparan sin busy-wait hasta alcanzar el reloj
    → el audio total dura exactamente lo que debe (mantiene 8 kHz medio).

    Si nos atrasamos > 50 ms (network hiccup raro), re-anclamos para no
    causar un "chipmunk" de catch-up demasiado largo.
    """
    global reproduciendo, estado_robot
    estado_robot = 'HABLANDO'
    reproduciendo = True

    received = 0
    play_pos = 0
    play_len = 0


    conn_spk.setblocking(True)
    conn_spk.settimeout(3.0)
    mv = memoryview(recv_buf)

    start = _ticks_us()
    sample_idx = 0

    try:
        while received < total or play_pos < play_len:
            # refill del buffer cuando se vacía
            if play_pos >= play_len:
                if received >= total:
                    break
                want = total - received
                if want > SPK_RECV_SIZE:
                    want = SPK_RECV_SIZE
                n = conn_spk.readinto(mv, want)
                if not n:
                    return
                received += n
                play_len = n
                play_pos = 0
                # Re-anclamos la base de tiempo para evitar desfases acumulados por la recarga del socket
                start = _ticks_us()
                sample_idx = 0

            # ── busy-wait absoluto sobre el target del sample (evita lentitud) ──
            target_us = sample_idx * INTERVAL_US
            while _ticks_diff(_ticks_us(), start) < target_us:
                pass

            _dac_write(recv_buf[play_pos])
            play_pos += 1
            sample_idx += 1
    finally:
        conn_spk.setblocking(False)
        _dac_write(128)
        # IMPORTANTE: cambiar el estado ANTES de bajar reproduciendo
        # para que el hilo OLED no dibuje 'HABLANDO' en el flanco final.
        estado_robot = 'ESPERANDO'
        reproduciendo = False
        gc.enable()
        gc.collect()


# ══════════════════════════════════════════════════════════════════
# HILO DE AUDIO
# ══════════════════════════════════════════════════════════════════

def hilo_audio():
    global estado_robot

    srv_mic = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
    srv_mic.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
    srv_mic.bind(('', PORT_MIC))
    srv_mic.listen(1)

    srv_spk = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
    srv_spk.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
    srv_spk.bind(('', PORT_SPK))
    srv_spk.listen(1)

    print('[Audio] Puertos:', PORT_MIC, '(mic)', PORT_SPK, '(spk)')

    # Buffers pre-asignados (no asignar memoria en el hot loop)
    mic_buf      = bytearray(MIC_CHUNK_SIZE)
    recv_buf     = bytearray(SPK_RECV_SIZE)
    header_buf   = bytearray(4)
    header_state = bytearray(1)

    while True:
        try:
            print('[Audio] Esperando laptop...')
            conn_mic, addr_m = srv_mic.accept()
            print('[Audio] mic conectado:', addr_m)
            conn_spk, addr_s = srv_spk.accept()
            print('[Audio] spk conectado:', addr_s)

            # mic: bloqueante con timeout corto (para no colgar el ESP32)
            conn_mic.setblocking(True)
            conn_mic.settimeout(1.0)
            # spk: no-bloqueante para el peek de headers en LISTEN
            conn_spk.setblocking(False)

            header_state[0] = 0
            dac.write(128)
            gc.collect()
            print('[Audio] Sesión iniciada (half-duplex).')

            while True:
                tam = listen_mode(conn_mic, conn_spk, mic_buf, header_buf, header_state)
                play_mode(conn_spk, tam, recv_buf)
                # tras tocar, vuelve a LISTEN automáticamente

        except OSError as e:
            print('[Audio] Sesión cerrada:', e)
        except Exception as e:
            print('[Audio] Error:', e)
        finally:
            estado_robot = 'ESPERANDO'
            try:
                conn_mic.close()
            except Exception:
                pass
            try:
                conn_spk.close()
            except Exception:
                pass
            dac.write(128)
            gc.collect()


# ══════════════════════════════════════════════════════════════════
# HILO OLED — animación CONTINUA con máxima vivacidad
# ══════════════════════════════════════════════════════════════════
# El módulo oled_ojos.tick() hace TODO el trabajo emocional:
#   - Transición lerp entre estados (5 frames)
#   - Microsacadas en estados conscientes
#   - Sleepy progresivo después de 15 s en ESPERANDO
#   - Pupilas siguiendo en SIGUIENDO
#   - Boca animada en HABLANDO según mouth_amp
# El hilo solo:
#   - Calcula idle_ms desde el último cambio de estado
#   - Calcula mouth_amp para HABLANDO (oscilación tipo seno)
#   - Dispara parpadeos aleatorios cada 3-7 s en estados despiertos
#   - Reduce la cadencia durante reproduciendo para no destrozar el DAC

OLED_FPS_MS_NORMAL   = 100     # ~10 fps en estados sin audio competiendo
OLED_FPS_MS_AUDIO    = 220     # ~4.5 fps cuando reproduce (audio prioritario)
OLED_BLINK_MIN_MS    = 3000    # parpadeo aleatorio cada [3..7] s
OLED_BLINK_MAX_MS    = 7000

def hilo_oled():
    if not OLED_DISPONIBLE:
        return

    while True:
        try:
            gc.collect()
            oled_ojos.reset_state()

            state_started_at = utime.ticks_ms()
            last_state       = ''
            next_blink_at    = utime.ticks_ms() + urandom.getrandbits(12) + OLED_BLINK_MIN_MS
            frame_n          = 0

            while True:
                ahora  = utime.ticks_ms()
                estado = estado_robot

                # Cambio de estado: reset timers
                if estado != last_state:
                    state_started_at = ahora
                    last_state = estado
                    frame_n = 0
                    next_blink_at = ahora + OLED_BLINK_MIN_MS + (urandom.getrandbits(12) % (OLED_BLINK_MAX_MS - OLED_BLINK_MIN_MS))

                idle_ms = utime.ticks_diff(ahora, state_started_at)

                # Boca animada en HABLANDO (oscilación tipo seno)
                mouth = 0.0
                if estado == 'HABLANDO':
                    mouth = abs(math.sin(frame_n * 0.45)) * 0.85
                    frame_n += 1

                # Parpadeo aleatorio: solo en estados despiertos, no
                # durante el sueño profundo (idle_ms > 30 s en ESPERANDO).
                blink_ok = (
                    not reproduciendo                # no parpadear durante PLAY
                    and estado in ('ESPERANDO', 'ESCUCHANDO', 'HABLANDO', 'CURIOSO')
                    and not (estado == 'ESPERANDO' and idle_ms > 30000)
                )
                if blink_ok and ahora >= next_blink_at:
                    try:
                        if urandom.getrandbits(3) == 0:    # 1/8 de los blinks = guiño
                            oled_ojos.do_wink('left' if urandom.getrandbits(1) else 'right')
                        else:
                            oled_ojos.do_blink()
                    except Exception as e:
                        print('[OLED] blink err:', e)
                    next_blink_at = ahora + OLED_BLINK_MIN_MS + (urandom.getrandbits(12) % (OLED_BLINK_MAX_MS - OLED_BLINK_MIN_MS))

                # Frame normal
                try:
                    oled_ojos.tick(estado,
                                   sig_dx=sig_dx, sig_dy=sig_dy,
                                   idle_ms=idle_ms,
                                   mouth_amp=mouth)
                except Exception as e:
                    print('[OLED] frame err:', e)

                # Sleep adaptativo según si el audio está activo
                if reproduciendo:
                    utime.sleep_ms(OLED_FPS_MS_AUDIO)
                else:
                    utime.sleep_ms(OLED_FPS_MS_NORMAL)

        except Exception as e:
            print('[OLED] hilo crash, reiniciando:', e)
            utime.sleep(1)


# ══════════════════════════════════════════════════════════════════
# ARRANQUE
# ══════════════════════════════════════════════════════════════════

conectar_wifi()

_thread.start_new_thread(hilo_audio, ())
print('Hilo de audio iniciado')

if OLED_DISPONIBLE:
    _thread.start_new_thread(hilo_oled, ())
    print('Hilo OLED iniciado')


# ══════════════════════════════════════════════════════════════════
# PARSER DE COMANDOS (compartido WiFi + USB)
# ══════════════════════════════════════════════════════════════════
# Formatos aceptados:
#   H:90,V:45               -> mover servos
#   ESTADO:FELIZ            -> sobrescribir el estado (afecta el OLED)
#   SIGUIENDO:0.12,-0.34    -> coord normalizadas del rostro a seguir

def aplicar_cmd(cmd):
    """Procesa UN comando de texto (sin salto de línea). Devuelve respuesta str."""
    global estado_robot, sig_dx, sig_dy
    if not cmd:
        return ''
    if cmd.startswith('H:'):
        h, v = parsear_servo(cmd)
        if h is not None:
            mover_servos(h, v)
            return 'OK H:%d V:%d\n' % (h, v)
        return 'ERR formato incorrecto\n'
    elif cmd.startswith('ESTADO:'):
        estado_robot = cmd.split(':', 1)[1]
        return 'OK ESTADO:%s\n' % estado_robot
    elif cmd.startswith('SIGUIENDO:'):
        partes = cmd.split(':', 1)[1].split(',')
        sig_dx = float(partes[0])
        sig_dy = float(partes[1])
        estado_robot = 'SIGUIENDO'
        return 'OK SIGUIENDO\n'
    return 'ERR comando desconocido\n'


# ══════════════════════════════════════════════════════════════════
# LOOP PRINCIPAL — comandos por WiFi (TCP) + USB serial (fallback)
# ══════════════════════════════════════════════════════════════════
# Canal primario: servidor TCP en PORT_CTRL (la laptop conecta por WiFi).
# Canal fallback: USB serial (sys.stdin) — sigue funcionando en paralelo,
# así un cable conectado a Thonny también puede mandar comandos.

srv_ctrl = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
srv_ctrl.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
srv_ctrl.bind(('', PORT_CTRL))
srv_ctrl.listen(1)
srv_ctrl.setblocking(False)

conn_ctrl = None
buf_ctrl  = b''
print('Loop de control listo. WiFi puerto', PORT_CTRL, '+ USB serial (fallback).')

while True:
    # ── A) WiFi: aceptar/atender al cliente de control ───────────────
    if conn_ctrl is None:
        try:
            conn_ctrl, addr = srv_ctrl.accept()
            conn_ctrl.setblocking(False)
            print('[Ctrl] Laptop conectada:', addr)
        except OSError:
            pass                              # nadie conectando ahora

    if conn_ctrl is not None:
        try:
            data = conn_ctrl.recv(256)
            if data == b'':                    # conexión cerrada por la laptop
                print('[Ctrl] Laptop desconectada')
                conn_ctrl.close()
                conn_ctrl = None
                buf_ctrl  = b''
            else:
                buf_ctrl += data
                # COALESCE: el TCP entrega los comandos en lote. Aplicar TODOS
                # los 'H:' del lote haría que el servo salte por posiciones
                # intermedias en <1 ms → espasmo. Guardamos solo el ÚLTIMO 'H:'
                # y lo movemos una sola vez por iteración (igual que el path USB,
                # paced ~20 ms). ESTADO/SIGUIENDO se aplican al toque (última gana).
                ultimo_h = None
                while b'\n' in buf_ctrl:
                    linea, buf_ctrl = buf_ctrl.split(b'\n', 1)
                    cmd = linea.decode().strip()
                    if cmd.startswith('H:'):
                        ultimo_h = cmd         # diferir: solo el último mueve
                    else:
                        try:
                            aplicar_cmd(cmd)    # ESTADO / SIGUIENDO
                        except Exception as e:
                            print('[Ctrl] cmd malo:', cmd, e)
                if ultimo_h is not None:
                    try:
                        aplicar_cmd(ultimo_h)   # un solo movimiento de servo
                    except Exception as e:
                        print('[Ctrl] servo malo:', ultimo_h, e)
                # Nota: NO respondemos por TCP. El SerialManager del laptop solo
                # escribe, nunca lee; mandar 'OK..' llenaría el buffer de envío.
        except OSError as e:
            # EAGAIN = simplemente no hay datos; cualquier otro = cerrar sesión
            if not (e.args and e.args[0] == EAGAIN):
                try:
                    conn_ctrl.close()
                except Exception:
                    pass
                conn_ctrl = None
                buf_ctrl  = b''

    # ── B) USB serial (fallback en paralelo) ─────────────────────────
    listo, _, _ = select.select([sys.stdin], [], [], 0)
    if listo:
        try:
            resp = aplicar_cmd(sys.stdin.readline().strip())
            if resp:
                sys.stdout.write(resp)
        except Exception as e:
            sys.stdout.write('ERR %s\n' % e)

    # No saturar el CPU. La laptop envía ~12 cmd/s en seguimiento facial.
    utime.sleep_ms(20)
