# audio_laptop.py  — ejecutar en la laptop con Python

import socket, struct, numpy as np, asyncio, edge_tts, os, time
import speech_recognition as sr
from langdetect import detect, LangDetectException
from scipy.io import wavfile
from scipy.signal import butter, sosfilt
import tempfile

# ── Configuración ────────────────────────────────────────
ESP32_IP   = '192.168.0.23'  # ← PON LA IP DE TU ESP32 AQUÍ
PORT_MIC   = 5005
PORT_SPK   = 5006
SAMPLE_RATE = 8000           # Hz — DEBE coincidir con el ESP32 (audio_server_esp32.py)
VOZ_ES     = 'es-MX-DaliaNeural'
VOZ_EN     = 'en-US-AriaNeural'

RECONEXION_SEG = 3           # segundos de espera antes de reintentar conexión

# Ruta absoluta del WAV de debug (se guarda SIEMPRE)
DEBUG_WAV = os.path.abspath('debug_audio.wav')

# ── Reconocedor de voz ───────────────────────────────────
reconocedor = sr.Recognizer()

def detectar_idioma(texto):
    try:
        lang = detect(texto)
        return lang if lang in ['es', 'en'] else 'es'
    except LangDetectException:
        return 'es'

async def generar_tts(texto, voz, archivo):
    await edge_tts.Communicate(texto, voz).save(archivo)

def tts_a_bytes(texto, idioma):
    """Genera TTS y retorna bytes de audio 8-bit a SAMPLE_RATE para el ESP32."""
    voz = VOZ_ES if idioma == 'es' else VOZ_EN
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        tmp_mp3 = f.name
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        tmp_wav = f.name
    try:
        asyncio.run(generar_tts(texto, voz, tmp_mp3))
        # Convertir MP3 → WAV mono 8-bit a SAMPLE_RATE Hz con ffmpeg.
        # OJO: -ar debe coincidir con la tasa del ESP32, de lo contrario
        # la reproducción suena al doble de lento/rápido.
        #
        # Filtros de audio para que el altavoz PAM8403 (pequeño, 8-bit) se
        # entienda mejor:
        #   highpass=200  → quita retumbe/DC que satura el altavoz
        #   lowpass=3600  → anti-aliasing por debajo de Nyquist (4000 Hz)
        #   dynaudnorm    → sube y nivela el volumen de la voz (clave en 8-bit)
        #   alimiter      → evita recortes bruscos al cuantizar a 8-bit
        filtros = 'highpass=f=200,lowpass=f=3600,dynaudnorm=f=200:g=15,alimiter=limit=0.95'
        ret = os.system(
            f'ffmpeg -y -i "{tmp_mp3}" -af "{filtros}" '
            f'-ar {SAMPLE_RATE} -ac 1 -acodec pcm_u8 '
            f'"{tmp_wav}" -loglevel quiet'
        )
        if ret != 0:
            print(f'[TTS] ffmpeg devolvió código {ret}')
        _, datos = wavfile.read(tmp_wav)
        return datos.tobytes()
    finally:
        for tmp in (tmp_mp3, tmp_wav):
            try:
                os.remove(tmp)
            except OSError:
                pass

def bytes_a_texto(datos_bytes):
    """Convierte bytes de audio crudo (uint8) del ESP32 a texto via STT.
    Diagnóstico explícito paso a paso para poder depurar fallos."""
    print('─' * 50)
    print(f'[STT] 1) Bytes recibidos: {len(datos_bytes)}')
    if not datos_bytes:
        print('[STT] ABORTA: no llegaron bytes')
        return None

    try:
        # uint8 (0..255) → float normalizado (-1..1) → int16
        audio_np = np.frombuffer(datos_bytes, dtype=np.uint8).astype(np.float32)
        print(f'[STT] 2) Array uint8: shape={audio_np.shape}, '
              f'min={audio_np.min():.0f}, max={audio_np.max():.0f}')
    except Exception as e:
        print(f'[STT] FALLO en frombuffer/cast: {e}')
        return None

    try:
        # 3a) Quitar la componente continua REAL (la media), no un 128 fijo:
        #     el MAX9814 no siempre centra exactamente en 128.
        audio_np = audio_np - audio_np.mean()

        # 3b) Filtro pasa-banda de voz (80–3600 Hz) para limpiar zumbido
        #     de red, DC residual y ruido por encima de la banda útil.
        sos = butter(4, [80, 3600], btype='band', fs=SAMPLE_RATE, output='sos')
        audio_np = sosfilt(sos, audio_np)

        # 3c) Normalizar amplitud (AGC simple) para aprovechar todo el rango
        #     int16 → Google reconoce mejor una señal con buen nivel.
        pico = np.max(np.abs(audio_np))
        if pico > 1.0:                       # hay señal real, no solo silencio
            audio_np = audio_np / pico * (0.9 * 32767)
        audio_int16 = audio_np.astype(np.int16)
        print(f'[STT] 3) Array int16: shape={audio_int16.shape}, '
              f'pico={pico:.0f}, dtype={audio_int16.dtype}')
    except Exception as e:
        print(f'[STT] FALLO en filtrado/normalización a int16: {e}')
        return None

    # Guardar SIEMPRE el WAV de debug (ruta absoluta) a SAMPLE_RATE Hz
    try:
        wavfile.write(DEBUG_WAV, SAMPLE_RATE, audio_int16)
        print(f'[STT] 4) WAV debug guardado: {DEBUG_WAV} '
              f'({SAMPLE_RATE} Hz, {audio_int16.size} muestras, '
              f'{audio_int16.size / SAMPLE_RATE:.2f} s)')
    except Exception as e:
        print(f'[STT] FALLO al escribir WAV debug: {e}')

    # WAV temporal para SpeechRecognition (mismo SAMPLE_RATE)
    tmp_wav = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            tmp_wav = f.name
        wavfile.write(tmp_wav, SAMPLE_RATE, audio_int16)
        print(f'[STT] 5) WAV temporal STT: {tmp_wav}')
        with sr.AudioFile(tmp_wav) as source:
            audio = reconocedor.record(source)
        print('[STT] 6) Audio cargado en reconocedor, llamando a Google...')
    except Exception as e:
        print(f'[STT] FALLO preparando audio para STT: {e}')
        return None
    finally:
        if tmp_wav:
            try:
                os.remove(tmp_wav)
            except OSError:
                pass

    try:
        texto = reconocedor.recognize_google(audio, language='es-ES')
        print(f'[STT] 7) Google reconoció: "{texto}"')
        return texto
    except sr.UnknownValueError:
        print('[STT] 7) Google no entendió el audio (UnknownValueError)')
        return None
    except sr.RequestError as e:
        print(f'[STT] 7) Error de red/API en Google STT: {e}')
        return None

def procesar_sesion(sock_mic, sock_spk):
    """Bucle de una conexión activa. Lanza OSError si la conexión se cae,
    para que el llamador reintente."""
    print('Conectado. Esperando audio del Creeper...')
    while True:
        # 1. Recibir audio del ESP32 (4 bytes longitud + datos crudos)
        header = sock_mic.recv(4)
        if not header or len(header) < 4:
            raise OSError('Conexión de micrófono cerrada por el ESP32')
        tam = struct.unpack('>I', header)[0]
        datos = b''
        while len(datos) < tam:
            chunk = sock_mic.recv(min(4096, tam - len(datos)))
            if not chunk:
                raise OSError('Conexión cortada mientras se recibía audio')
            datos += chunk
        print(f'Recibidos {len(datos)} bytes de audio')

        # 2. STT
        texto = bytes_a_texto(datos)
        if not texto:
            print('Audio no entendido, ignorando.')
            continue
        print(f'Escuché: {texto}')

        # 3. Detectar idioma
        idioma = detectar_idioma(texto)
        print(f'Idioma detectado: {idioma}')

        # 4. Generar respuesta (Guía 4 agregará IA aquí)
        if idioma == 'es':
            respuesta = f'Ssssss... dijiste: {texto}'
        else:
            respuesta = f'Ssss... you said: {texto}'

        # 5. TTS → bytes → enviar al ESP32
        audio_respuesta = tts_a_bytes(respuesta, idioma)
        sock_spk.sendall(struct.pack('>I', len(audio_respuesta)) + audio_respuesta)
        print(f'Respuesta enviada: {len(audio_respuesta)} bytes')

def main():
    # ── Reconexión automática (fix 5) ────────────────────────
    while True:
        sock_mic = sock_spk = None
        try:
            print(f'Conectando al ESP32 en {ESP32_IP}...')
            sock_mic = socket.socket()
            sock_mic.connect((ESP32_IP, PORT_MIC))
            sock_spk = socket.socket()
            sock_spk.connect((ESP32_IP, PORT_SPK))
            procesar_sesion(sock_mic, sock_spk)
        except (OSError, ConnectionError) as e:
            print(f'[RED] Conexión perdida: {e}')
        except KeyboardInterrupt:
            print('\nDetenido por el usuario.')
            break
        finally:
            for s in (sock_mic, sock_spk):
                if s:
                    try:
                        s.close()
                    except OSError:
                        pass
        print(f'[RED] Reintentando en {RECONEXION_SEG} s...')
        time.sleep(RECONEXION_SEG)

if __name__ == '__main__':
    main()
