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

            # ── busy-wait absoluto al target del sample ─────────
            target_us = sample_idx * INTERVAL_US
            while _ticks_diff(_ticks_us(), start) < target_us:
                pass

            _dac_write(recv_buf[play_pos])
            play_pos += 1
            sample_idx += 1

            # ── cap del catch-up: si atrasados > 50 ms, re-anclar ─
            # (sin esto, tras un blocking de TCP largo el DAC dispara
            # cientos de samples sin espera y suena tipo chipmunk)
            if (sample_idx & 1023) == 0:
                drift = _ticks_diff(_ticks_us(), start) - sample_idx * INTERVAL_US
                if drift > 50000:
                    start = _ticks_us()
                    sample_idx = 0
    finally:
        conn_spk.setblocking(False)
        _dac_write(128)
        # IMPORTANTE: cambiar el estado ANTES de bajar reproduciendo
        # para que el hilo OLED no dibuje 'HABLANDO' en el flanco final.
        estado_robot = 'ESPERANDO'
        reproduciendo = False


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
# HILO OLED — renderiza el estado emocional con MÍNIMO impacto en audio
# ══════════════════════════════════════════════════════════════════
# El SH1106 por SoftI2C tarda ~25 ms en cada disp.show() — eso bloquea
# todo el busy-wait del audio y produce voz lenta + mic pierde samples.
# Estrategia:
#   - DURANTE reproduciendo: 0 actualizaciones (excepto el flanco de
#     entrada). Audio limpio.
#   - Estados estáticos (ESCUCHANDO/PENSANDO/FELIZ/CURIOSO): 1 sola
#     llamada a show() al CAMBIAR de estado, después idle.
#   - HABLANDO/SIGUIENDO/parpadeo: animaciones a 12 fps cuando no hay
#     audio compitiendo.
#   - Sleep adaptativo: 80 ms cuando anima, 250 ms cuando está idle.

OLED_FPS_MS_ACTIVE = 80       # ~12 fps cuando hay animación
OLED_FPS_MS_IDLE   = 250      # ~4  fps cuando todo está estático
OLED_FPS_MS_AUDIO  = 300      # casi dormido durante PLAY
PARPADEO_MS        = 4000     # blink natural cada 4 s en ESPERANDO

def _dibujar_estatico(estado):
    """Dibuja un estado estático (no anima). Usado solo en flancos."""
    if   estado == 'ESPERANDO':  ojos_normal()
    elif estado == 'ESCUCHANDO': ojos_abiertos()
    elif estado == 'PENSANDO':   ojos_pensando()
    elif estado == 'HABLANDO':   ojos_hablando(0)
    elif estado == 'FELIZ':      ojos_feliz()
    elif estado == 'CURIOSO':    ojos_curioso()

def hilo_oled():
    global frame_habla

    if not OLED_DISPONIBLE:
        return

    # Bucle exterior: reinicia si el hilo crashea catastróficamente.
    while True:
        try:
            gc.collect()
            ultimo_parpadeo = utime.ticks_ms()
            last_state = ''            # fuerza el primer dibujo
            last_dxq   = -999
            last_dyq   = -999

            try:
                ojos_normal()
                last_state = 'ESPERANDO'
            except Exception as e:
                print('[OLED] init err:', e)
                utime.sleep(2)
                continue

            while True:
                ahora  = utime.ticks_ms()
                estado = estado_robot      # snapshot atómico
                cambio = (estado != last_state)

                # ── 1) AUDIO REPRODUCIENDO: prácticamente dormimos ──
                if reproduciendo:
                    if cambio:
                        # único draw permitido durante PLAY: cuando el
                        # laptop manda FELIZ/HABLANDO/etc. al entrar.
                        try:
                            _dibujar_estatico(estado)
                            last_state = estado
                        except Exception as e:
                            print('[OLED] play err:', e)
                    utime.sleep_ms(OLED_FPS_MS_AUDIO)
                    continue

                # ── 2) Estados estáticos: 1 draw por cambio ─────────
                try:
                    if estado in ('ESCUCHANDO', 'PENSANDO', 'FELIZ', 'CURIOSO'):
                        if cambio:
                            _dibujar_estatico(estado)
                            last_state = estado
                        utime.sleep_ms(OLED_FPS_MS_IDLE)
                        continue

                    # ── 3) ESPERANDO: estático + parpadeo cada 4 s ──
                    if estado == 'ESPERANDO':
                        if cambio:
                            ojos_normal()
                            last_state = estado
                            ultimo_parpadeo = ahora
                        elif utime.ticks_diff(ahora, ultimo_parpadeo) > PARPADEO_MS:
                            parpadear()
                            ojos_normal()
                            ultimo_parpadeo = ahora
                        utime.sleep_ms(OLED_FPS_MS_IDLE)
                        continue

                    # ── 4) HABLANDO sin reproducir (raro): anima ────
                    if estado == 'HABLANDO':
                        ojos_hablando(frame_habla)
                        frame_habla += 1
                        last_state = estado
                        utime.sleep_ms(OLED_FPS_MS_ACTIVE)
                        continue

                    # ── 5) SIGUIENDO: redraw solo si las pupilas se mueven ─
                    if estado == 'SIGUIENDO':
                        # cuantizar dx/dy a pasos de 10% para evitar redraws
                        # por ruido fino del tracker
                        dxq = int(sig_dx * 10)
                        dyq = int(sig_dy * 10)
                        if cambio or dxq != last_dxq or dyq != last_dyq:
                            ojos_siguiendo(sig_dx, sig_dy)
                            last_state = estado
                            last_dxq = dxq
                            last_dyq = dyq
                        utime.sleep_ms(OLED_FPS_MS_ACTIVE)
                        continue

                except Exception as e:
                    print('[OLED] frame err:', e)

                # estado desconocido: idle
                utime.sleep_ms(OLED_FPS_MS_IDLE)

        except Exception as e:
            # Hilo crasheó (ej. I2C colgado): reintentar después de 1 s
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
