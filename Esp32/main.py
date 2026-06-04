
# audio_server_esp32.py — guardar en el ESP32 DevKit como main.py
# Ejecutar desde Thonny con F5

from machine import ADC, DAC, Pin
import network, usocket, utime, struct, gc
from config import SSID, PASSWORD   # credenciales fuera del repo (config.py gitignored)

# ── Configuración ─────────
PORT_MIC    = 5005       # Puerto TCP para enviar audio del micrófono
PORT_SPK    = 5006       # Puerto TCP para recibir audio del speaker
SAMPLE_RATE = 8000       # Hz — calidad telefónica: cubre toda la voz humana (~3.4 kHz).
                         # 8000 Hz = 125 µs/muestra, el máximo práctico del loop Python.
                         # DEBE coincidir con SAMPLE_RATE en audio_laptop.py.
INTERVALO   = 1_000_000 // SAMPLE_RATE  # microsegundos entre muestras = 125µs
SEGUNDOS    = 3          # Duración de captura por turno

# ── Hardware ──────────────────────────────────────────────────────
adc = ADC(Pin(34))           # GPIO34 = ADC1_CH6 (funciona con WiFi activo)
adc.atten(ADC.ATTN_11DB)    # Rango completo 0 – 3.3V
adc.width(ADC.WIDTH_12BIT)  # 12 bits de resolución (0 – 4095)

dac = DAC(Pin(25))           # GPIO25 = DAC1

# ── WiFi ──────────────────────────────────────────────────────────
def conectar_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print('Ya conectado. IP:', ip)
        return ip
    print('Conectando al WiFi...')
    wlan.connect(SSID, PASSWORD)
    for _ in range(30):           # espera hasta 15 segundos
        if wlan.isconnected():
            ip = wlan.ifconfig()[0]
            print('Conectado! IP del ESP32:', ip)
            return ip
        utime.sleep(0.5)
        print('.', end='')
    raise RuntimeError('No se pudo conectar al WiFi')

# ── Detección de voz por energía ──────────────────────────────────
def hay_voz():
    """
    Detección robusta por MÚLTIPLES ráfagas para distinguir voz real
    del ruido eléctrico del ESP32.

    El ruido eléctrico produce picos altos pero ESPORÁDICOS (1 ráfaga
    aislada), mientras que la voz humana es SOSTENIDA (3-5 ráfagas).
    Una sola ráfaga no basta: el ruido (~1687) supera incluso a la voz
    (~492), así que medimos la persistencia, no solo el pico.

    Toma 5 ráfagas de 100 muestras con 20 ms de pausa entre ellas y
    cuenta cuántas superan UMBRAL_VOZ. Devuelve True solo si al menos
    3 de 5 lo superan.
    """
    UMBRAL_VOZ = 400
    activas = 0
    for _ in range(5):
        muestras = [adc.read() for _ in range(100)]
        energia = max(muestras) - min(muestras)
        if energia > UMBRAL_VOZ:
            activas += 1
        utime.sleep_ms(20)          # pausa entre ráfagas
    return activas >= 3

# ── Captura de audio con timing controlado ────────────────────────
def capturar_audio(segundos=SEGUNDOS):
    """
    Captura audio del ADC a SAMPLE_RATE Hz real.
    Usa utime.ticks_us() para respetar el intervalo entre muestras.
    Retorna bytes de audio 8-bit.
    """
    total = SAMPLE_RATE * segundos
    datos = bytearray(total)
    for i in range(total):
        t0 = utime.ticks_us()
        datos[i] = adc.read() >> 4           # 12-bit → 8-bit (0–255)
        # Esperar el resto del intervalo para mantener la tasa de muestreo
        while utime.ticks_diff(utime.ticks_us(), t0) < INTERVALO:
            pass
    return bytes(datos)

# ── Reproducción de audio con timing controlado ───────────────────
def reproducir_audio(datos):
    """
    Reproduce bytes de audio (8-bit) por el DAC a SAMPLE_RATE Hz.
    Usa el mismo intervalo que la captura para reproducir a la
    velocidad correcta.
    """
    for byte in datos:
        t0 = utime.ticks_us()
        dac.write(byte)
        while utime.ticks_diff(utime.ticks_us(), t0) < INTERVALO:
            pass
    dac.write(128)   # punto medio = silencio al terminar

# ── Recibir y reproducir en streaming (sin acumular en RAM) ───────
def recibir_y_reproducir(conn, tam):
    """
    Recibe 'tam' bytes de audio en chunks y los reproduce por el DAC al
    vuelo, SIN acumular todo en RAM (evita MemoryError en el heap).

    Para que la voz salga FLUIDA (sin micro-cortes):
      - Buffer preasignado + readinto(): NO se asigna memoria dentro del
        loop, así el recolector de basura no se dispara a media
        reproducción (era la causa principal del entrecortado).
      - gc.collect() una sola vez antes de empezar, con el heap limpio.
    """
    chunk_size = 512
    buf = bytearray(chunk_size)
    mv  = memoryview(buf)
    gc.collect()                 # heap limpio antes de reproducir
    recibidos = 0
    while recibidos < tam:
        pedir = tam - recibidos
        if pedir > chunk_size:
            pedir = chunk_size
        n = conn.readinto(mv, pedir)   # llena 'buf' sin asignar memoria nueva
        if not n:
            break
        recibidos += n
        # Reproducir este chunk inmediatamente, muestra a muestra
        for j in range(n):
            t0 = utime.ticks_us()
            dac.write(buf[j])
            while utime.ticks_diff(utime.ticks_us(), t0) < INTERVALO:
                pass
    dac.write(128)   # punto medio = silencio al terminar

# ── Recibir datos completos por TCP ───────────────────────────────
def recibir_completo(conn, tam):
    """
    Recibe exactamente 'tam' bytes de la conexión TCP.
    TCP puede fragmentar los datos — este loop garantiza que
    se reciba todo antes de continuar.
    """
    datos = b''
    while len(datos) < tam:
        chunk = conn.recv(min(512, tam - len(datos)))
        if not chunk:
            break
        datos += chunk
    return datos

# ── Bucle principal ───────────────────────────────────────────────
def main():
    ip = conectar_wifi()
    print('ESP32 Audio Server listo en', ip)
    print('SAMPLE_RATE:', SAMPLE_RATE, 'Hz | deteccion de voz: 3 de 5 rafagas')
    print('Esperando conexion de la laptop...')

    # Servidor TCP — micrófono (ESP32 → laptop)
    srv_mic = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
    srv_mic.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
    srv_mic.bind(('', PORT_MIC))   # '' = todas las interfaces
    srv_mic.listen(1)

    # Servidor TCP — speaker (laptop → ESP32)
    srv_spk = usocket.socket(usocket.AF_INET, usocket.SOCK_STREAM)
    srv_spk.setsockopt(usocket.SOL_SOCKET, usocket.SO_REUSEADDR, 1)
    srv_spk.bind(('', PORT_SPK))
    srv_spk.listen(1)

    print(f'Puertos: {PORT_MIC} (mic salida) | {PORT_SPK} (speaker entrada)')

    # Bucle de reconexión: si la laptop se desconecta, volvemos a esperar
    # una nueva conexión en lugar de morir (fix 5).
    while True:
        # Aceptar conexiones de la laptop (bloquea hasta que conecte)
        print('Esperando conexion de la laptop...')
        conn_mic, addr = srv_mic.accept()
        print('Laptop conectada en mic:', addr)
        conn_spk, addr = srv_spk.accept()
        print('Laptop conectada en speaker:', addr)
        print('Sistema listo. Habla al microfono.')

        try:
            while True:
                # 1. Monitorear micrófono — esperar voz
                if not hay_voz():
                    continue

                # 2. Voz detectada — capturar audio
                print('Voz detectada — capturando', SEGUNDOS, 'segundos...')
                audio = capturar_audio(segundos=SEGUNDOS)
                print('Capturados', len(audio), 'bytes')

                # 3. Enviar al servidor Python en la laptop
                #    Formato: 4 bytes de longitud (big-endian) + datos
                longitud = struct.pack('>I', len(audio))
                conn_mic.sendall(longitud + audio)
                print('Enviados', len(audio), 'bytes a la laptop')

                # 4-5. Recibir y reproducir la respuesta EN STREAMING.
                #      Nunca se acumula todo el audio en RAM (evita MemoryError).
                header = conn_spk.recv(4)
                if not header or len(header) < 4:
                    raise OSError('Conexion del speaker cerrada')
                tam = struct.unpack('>I', header)[0]
                print('Reproduciendo en streaming:', tam, 'bytes...')
                recibir_y_reproducir(conn_spk, tam)
                print('Listo. Esperando siguiente voz...')

        except OSError as e:
            # Conexión perdida: cerramos clientes y volvemos a accept()
            print('Conexion perdida:', e, '- esperando reconexion...')
        finally:
            try:
                conn_mic.close()
            except OSError:
                pass
            try:
                conn_spk.close()
            except OSError:
                pass
            dac.write(128)   # dejar el speaker en silencio

main()