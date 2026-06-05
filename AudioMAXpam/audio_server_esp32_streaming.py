# audio_server_esp32_streaming.py
# Servidor TCP HALF-DUPLEX para Creeper VoiceChat.
# Guardar en el ESP32 como main.py y ejecutar.
#
# Diseño: state machine sin timer ISR (los ISRs de MicroPython no sostienen
# 8 kHz por la sobrecarga de Python — el original con busy-wait sí funciona).
#
#   LISTEN -> muestrea ADC con busy-wait a 8 kHz, envía chunks de mic al laptop,
#             y periódicamente revisa si llegó un header del speaker.
#   PLAY   -> al recibir un header con longitud > 0, lee del socket en chunks
#             y los escupe al DAC con busy-wait a 8 kHz. Mic apagado.
#   Vuelve a LISTEN cuando termina el playback.
#
# Protocolo (sin cambios respecto a antes):
#   PORT_MIC (ESP32 -> laptop): stream continuo uint8 8 kHz, sin headers.
#   PORT_SPK (laptop -> ESP32): | 4 bytes BE length | N bytes payload |
#       length == 0  o 0xFFFFFFFE -> ignorar (keepalive / no-op)
#       length == 0xFFFFFFFF       -> ignorar (STOP, no se usa en half-duplex)
#       length >  0 normal         -> body de audio uint8

import micropython
from micropython import const
from machine import ADC, DAC, Pin
import network, usocket, ustruct, utime, gc
from config import SSID, PASSWORD


# ── Configuración ──────────────────────────────────────────────
PORT_MIC    = 5005
PORT_SPK    = 5006
SAMPLE_RATE = 8000

_INTERVAL_US = const(125)        # 1_000_000 // 8000

_MIC_CHUNK   = const(256)        # bytes por paquete TCP de mic (32 ms)
_SPK_RECV    = const(1024)       # bytes que pedimos al socket en cada refill
_SPK_CHECK_EVERY = const(32)     # samples entre cada peek del socket spk

EAGAIN = 11

micropython.alloc_emergency_exception_buf(100)


# ── Hardware ───────────────────────────────────────────────────
adc = ADC(Pin(34))
adc.atten(ADC.ATTN_11DB)
adc.width(ADC.WIDTH_12BIT)
dac = DAC(Pin(25))

# Cachés (acceso a local es ~3x más rápido que atributo)
_adc_read   = adc.read
_dac_write  = dac.write
_ticks_us   = utime.ticks_us
_ticks_diff = utime.ticks_diff


# ── WiFi ───────────────────────────────────────────────────────
def wifi_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print('Conectando WiFi...')
        wlan.connect(SSID, PASSWORD)
        for _ in range(30):
            if wlan.isconnected():
                break
            utime.sleep(0.5)
            print('.', end='')
    if not wlan.isconnected():
        raise RuntimeError('Sin WiFi')
    ip = wlan.ifconfig()[0]
    print('\nIP del ESP32:', ip)
    return ip


# ── Modo LISTEN: muestrea mic + revisa header del speaker ──────
# Devuelve la longitud del próximo mensaje de audio (> 0) cuando llega.
# Si llega un código de control, simplemente sigue escuchando.
# (Sin @micropython.native: el busy-wait domina el timing y native tiene
#  quirks con excepciones; el firmware original también funcionaba sin él.)
def listen_mode(conn_mic, conn_spk, mic_buf, header_buf, header_state):
    pos = 0
    chk = 0

    while True:
        t0 = _ticks_us()

        # === sample mic ===
        mic_buf[pos] = _adc_read() >> 4
        pos += 1
        if pos >= _MIC_CHUNK:
            try:
                conn_mic.send(mic_buf)
            except OSError as e:
                if not (e.args and e.args[0] == EAGAIN):
                    raise e
            pos = 0

        # === cada N samples, ver si llegó algo del speaker ===
        chk += 1
        if chk >= _SPK_CHECK_EVERY:
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
                        tam = ustruct.unpack('>I', header_buf)[0]
                        header_state[0] = 0
                        # códigos de control: ignorar y seguir escuchando
                        if tam > 0 and tam != 0xFFFFFFFF and tam != 0xFFFFFFFE:
                            return tam
            except OSError as e:
                if not (e.args and e.args[0] == EAGAIN):
                    raise e

        # busy-wait al siguiente sample (timing exacto)
        while _ticks_diff(_ticks_us(), t0) < _INTERVAL_US:
            pass


# ── Modo PLAY: drena 'total' bytes del socket y los toca al DAC ─
# Lee en chunks de _SPK_RECV en un buffer pre-asignado.
# El mic NO se muestrea: damos todo el CPU al DAC.
def play_mode(conn_spk, total, recv_buf):
    received = 0
    play_pos = 0
    play_len = 0

    # Para esta función necesitamos lecturas confiables — bloqueante con timeout
    conn_spk.setblocking(True)
    conn_spk.settimeout(3.0)
    mv = memoryview(recv_buf)

    try:
        while received < total or play_pos < play_len:
            # refill si nos quedamos sin samples y aún quedan por venir
            if play_pos >= play_len:
                if received >= total:
                    break
                want = total - received
                if want > _SPK_RECV:
                    want = _SPK_RECV
                n = conn_spk.readinto(mv, want)
                if not n:
                    return  # conexión cerrada
                received += n
                play_len = n
                play_pos = 0

            t0 = _ticks_us()
            _dac_write(recv_buf[play_pos])
            play_pos += 1
            # busy-wait estricto: 125 µs por sample SIEMPRE
            while _ticks_diff(_ticks_us(), t0) < _INTERVAL_US:
                pass
    finally:
        conn_spk.setblocking(False)
        _dac_write(128)   # silencio al terminar


# ── Main ───────────────────────────────────────────────────────
def main():
    wifi_connect()

    srv_mic = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
    srv_mic.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
    srv_mic.bind(('', PORT_MIC))
    srv_mic.listen(1)

    srv_spk = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
    srv_spk.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
    srv_spk.bind(('', PORT_SPK))
    srv_spk.listen(1)

    print('ESP32 listo. Puertos:', PORT_MIC, '(mic)', PORT_SPK, '(spk)')

    # Buffers pre-asignados (no allocar en el hot loop)
    mic_buf      = bytearray(_MIC_CHUNK)
    recv_buf     = bytearray(_SPK_RECV)
    header_buf   = bytearray(4)
    header_state = bytearray(1)   # [pos]

    while True:
        try:
            print('Esperando laptop...')
            conn_mic, addr_m = srv_mic.accept()
            print('mic conectado:', addr_m)
            conn_spk, addr_s = srv_spk.accept()
            print('spk conectado:', addr_s)

            # mic socket: bloqueante con timeout (el send debe poder ceder)
            conn_mic.setblocking(True)
            conn_mic.settimeout(1.0)
            # spk socket: no-bloqueante para el peek de headers en listen
            conn_spk.setblocking(False)

            header_state[0] = 0
            dac.write(128)
            gc.collect()
            print('Sesión iniciada (half-duplex).')

            while True:
                tam = listen_mode(conn_mic, conn_spk, mic_buf, header_buf, header_state)
                # llegó un body de audio: tocarlo
                play_mode(conn_spk, tam, recv_buf)
                # vuelve a LISTEN automáticamente

        except OSError as e:
            print('Sesión cerrada:', e)
        except Exception as e:
            print('Error:', e)
        finally:
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


main()
