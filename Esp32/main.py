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

# OLED — opcional: si el módulo no está, se omite el hilo OLED
try:
    from oled_ojos import (ojos_normal, ojos_abiertos, ojos_pensando,
                            ojos_hablando, ojos_feliz, ojos_curioso,
                            ojos_siguiendo, parpadear)
    OLED_DISPONIBLE = True
except Exception as _e_oled:
    OLED_DISPONIBLE = False
    print('OLED no disponible:', _e_oled)


# ══════════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════

PORT_MIC    = 5005
PORT_SPK    = 5006
SAMPLE_RATE = 8000
INTERVAL_US = 1_000_000 // SAMPLE_RATE   # 125 µs por muestra

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
    LISTEN: muestrea el ADC a 8 kHz exacto y manda chunks de 32 ms al laptop.
    Cada SPK_CHECK_EVERY samples (~4 ms) peek el socket spk; si llega un
    header de audio con length>0, lo devuelve y el caller cambia a PLAY.
    """
    global estado_robot
    estado_robot = 'ESCUCHANDO'

    pos = 0
    chk = 0
    while True:
        t0 = _ticks_us()

        # ── 1) sample mic ────────────────────────────────────────
        mic_buf[pos] = _adc_read() >> 4   # 12-bit -> 8-bit
        pos += 1
        if pos >= MIC_CHUNK_SIZE:
            try:
                conn_mic.send(mic_buf)
            except OSError as e:
                if not (e.args and e.args[0] == EAGAIN):
                    raise e
            pos = 0

        # ── 2) peek del socket spk para ver si llegó algo ────────
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
                        # ignorar códigos de control
                        if tam > 0 and tam != 0xFFFFFFFF and tam != 0xFFFFFFFE:
                            return tam
            except OSError as e:
                if not (e.args and e.args[0] == EAGAIN):
                    raise e

        # ── 3) busy-wait al siguiente sample (timing exacto) ─────
        while _ticks_diff(_ticks_us(), t0) < INTERVAL_US:
            pass


def play_mode(conn_spk, total, recv_buf):
    """
    PLAY: drena 'total' bytes del socket spk y los escupe al DAC a 8 kHz.
    El mic NO se muestrea: damos todo el CPU al DAC -> audio limpio.
    """
    global reproduciendo, estado_robot
    estado_robot = 'HABLANDO'
    reproduciendo = True

    received = 0
    play_pos = 0
    play_len = 0

    # bloqueante con timeout para recibir el body con seguridad
    conn_spk.setblocking(True)
    conn_spk.settimeout(3.0)
    mv = memoryview(recv_buf)

    try:
        while received < total or play_pos < play_len:
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

            t0 = _ticks_us()
            _dac_write(recv_buf[play_pos])
            play_pos += 1
            while _ticks_diff(_ticks_us(), t0) < INTERVAL_US:
                pass
    finally:
        conn_spk.setblocking(False)
        _dac_write(128)
        reproduciendo = False
        estado_robot = 'ESPERANDO'


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
# HILO OLED — renderiza el estado emocional a ~12 fps
# ══════════════════════════════════════════════════════════════════

def hilo_oled():
    global frame_habla
    if not OLED_DISPONIBLE:
        return

    ultimo_parpadeo = utime.ticks_ms()
    INTERVALO_PARPADEO = 4000   # parpadeo natural cada 4 s

    try:
        ojos_normal()
    except Exception as e:
        print('[OLED] error inicial:', e)
        return

    while True:
        ahora = utime.ticks_ms()
        estado = estado_robot   # snapshot atómico
        try:
            if estado == 'ESPERANDO':
                if utime.ticks_diff(ahora, ultimo_parpadeo) > INTERVALO_PARPADEO:
                    parpadear()
                    ultimo_parpadeo = ahora
                else:
                    ojos_normal()
            elif estado == 'ESCUCHANDO':
                ojos_abiertos()
            elif estado == 'PENSANDO':
                ojos_pensando()
            elif estado == 'HABLANDO':
                ojos_hablando(frame_habla)
                frame_habla += 1
            elif estado == 'FELIZ':
                ojos_feliz()
            elif estado == 'CURIOSO':
                ojos_curioso()
            elif estado == 'SIGUIENDO':
                ojos_siguiendo(sig_dx, sig_dy)
        except Exception as e:
            print('[OLED] err:', e)
        utime.sleep_ms(80)   # ~12 fps


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
# LOOP PRINCIPAL — comandos Serial desde la laptop (USB)
# ══════════════════════════════════════════════════════════════════
# Formatos aceptados:
#   H:90,V:45               -> mover servos
#   ESTADO:FELIZ            -> sobrescribir el estado (afecta el OLED)
#   SIGUIENDO:0.12,-0.34    -> coord normalizadas del rostro a seguir
print('Loop de servos listo. Esperando comandos Serial...')

while True:
    listo, _, _ = select.select([sys.stdin], [], [], 0)
    if listo:
        try:
            cmd = sys.stdin.readline().strip()
            if not cmd:
                pass
            elif cmd.startswith('H:'):
                h, v = parsear_servo(cmd)
                if h is not None:
                    mover_servos(h, v)
                    sys.stdout.write('OK H:%d V:%d\n' % (h, v))
                else:
                    sys.stdout.write('ERR formato incorrecto\n')
            elif cmd.startswith('ESTADO:'):
                estado_robot = cmd.split(':', 1)[1]
                sys.stdout.write('OK ESTADO:%s\n' % estado_robot)
            elif cmd.startswith('SIGUIENDO:'):
                partes = cmd.split(':', 1)[1].split(',')
                sig_dx = float(partes[0])
                sig_dy = float(partes[1])
                estado_robot = 'SIGUIENDO'
                sys.stdout.write('OK SIGUIENDO\n')
            else:
                sys.stdout.write('ERR comando desconocido\n')
        except Exception as e:
            sys.stdout.write('ERR %s\n' % e)

    # No saturar el CPU. La laptop envía ~12 cmd/s en seguimiento facial.
    utime.sleep_ms(20)
