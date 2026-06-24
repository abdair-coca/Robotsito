# audio_laptop.py — Guía 4 (Groq) — CLIENTE del ESP32 servidor (AudioMAXpam)
# Arquitectura: el ESP32 (audio_server_esp32.py) es el SERVIDOR y escucha en
# dos puertos; la LAPTOP se CONECTA a él. Audio CRUDO de 8 bits (sin cabecera
# WAV), protocolo: 4 bytes big-endian de longitud + datos.
#   - sock_mic (PORT_MIC): ESP32 -> laptop, voz del usuario
#   - sock_spk (PORT_SPK): laptop -> ESP32, respuesta hablada
# Flujo: recibir voz -> STT (Google) -> Groq -> TTS (edge-tts+ffmpeg) -> enviar.

import socket, struct, numpy as np, asyncio, edge_tts, os, time, sys
import tempfile, subprocess
import imageio_ffmpeg
import speech_recognition as sr
from scipy.io import wavfile
from scipy.signal import butter, sosfilt
from groq import Groq
from langdetect import detect, LangDetectException
from config import GROQ_API_KEY
from time import sleep

# Binario de ffmpeg incluido en imageio-ffmpeg (no depende de un ffmpeg global)
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# ── Red (debe coincidir con el ESP32) ─────────────────────
ESP32_IP   = '192.168.0.23'   # ← IP de tu ESP32 (la imprime al arrancar)
PORT_MIC   = 5005             # ESP32 -> laptop (voz). = PORT_MIC del ESP32
PORT_SPK   = 5006             # laptop -> ESP32 (respuesta). = PORT_SPK del ESP32
RECONEXION_SEG = 3            # espera antes de reintentar conexión

# ── Audio ─────────────────────────────────────────────────
SAMPLE_RATE = 8000            # Debe coincidir con el ESP32
VOZ_ES      = 'es-MX-DaliaNeural'
VOZ_EN      = 'en-US-AriaNeural'
VOLUMEN     = 0.6             # 0.0–1.0 — volumen del speaker

# ── Groq ──────────────────────────────────────────────────
GROQ_MODEL    = 'llama-3.3-70b-versatile'   # natural y rápido; o 'llama-3.1-8b-instant'
MAX_HISTORIAL = 8             # nº de mensajes recordados (4 turnos ida/vuelta)
RESET_MEMORIA_SEG = 60        # >60s sin hablar -> se olvida la charla (nuevo visitante)

cliente_groq = Groq(api_key=GROQ_API_KEY)
historial = []                # memoria de conversación: [{role, content}, ...]

SYSTEM_PROMPT = """Eres un robot interactivo Con sentimientos y personalidad amable y divertido.
Creado por Abdair, estudiante de Ingeniería Informática, UATF, Bolivia, tiene 20 años.
Responde en máximo 2 oraciones. Sin markdown ni formato especial.
Responde SIEMPRE en el mismo idioma de la pregunta."""

# ── STT/IA ────────────────────────────────────────────────
reconocedor = sr.Recognizer()

# Silencio (800 bytes a 128) que se envía cuando no se entiende el audio,
# para que el ESP32 no se quede bloqueado esperando respuesta.
SILENCIO = bytes([128] * 800)

def detectar_idioma(texto):
    try:
        lang = detect(texto)
        return lang if lang in ['es', 'en'] else 'es'
    except LangDetectException:
        return 'es'

def preguntar_groq(texto, idioma='es'):
    global historial
    historial.append({'role': 'user', 'content': texto})
    historial = historial[-MAX_HISTORIAL:]
    try:
        r = cliente_groq.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{'role': 'system', 'content': SYSTEM_PROMPT}] + historial,
            max_tokens=120, temperature=0.8, top_p=0.9,
        )
        respuesta = r.choices[0].message.content.strip()
        historial.append({'role': 'assistant', 'content': respuesta})
        historial = historial[-MAX_HISTORIAL:]
        return respuesta
    except Exception as e:
        print(f'Error Groq: {e}')
        if historial and historial[-1]['role'] == 'user':
            historial.pop()
        return 'Sssss... fallo de conexión.' if idioma == 'es' else 'Ssss... connection failed.'

def bytes_a_texto(datos_bytes):
    """Audio CRUDO 8-bit del ESP32 -> texto. Limpia la señal (DC, banda de voz,
    normalización) antes de Google STT. Devuelve (texto, idioma)."""
    if not datos_bytes:
        return None, 'es'
    # uint8 -> float; quitar la media real; pasa-banda de voz; normalizar a int16
    audio_np = np.frombuffer(datos_bytes, dtype=np.uint8).astype(np.float32)
    audio_np = audio_np - audio_np.mean()
    sos = butter(4, [80, 3600], btype='band', fs=SAMPLE_RATE, output='sos')
    audio_np = sosfilt(sos, audio_np)
    pico = np.max(np.abs(audio_np))
    if pico > 1.0:                       # hay señal real (no solo silencio)
        audio_np = audio_np / pico * (0.9 * 32767)
    audio_int16 = audio_np.astype(np.int16)

    tmp_wav = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            tmp_wav = f.name
        wavfile.write(tmp_wav, SAMPLE_RATE, audio_int16)
        with sr.AudioFile(tmp_wav) as source:
            audio = reconocedor.record(source)
    finally:
        if tmp_wav:
            try:
                os.remove(tmp_wav)
            except OSError:
                pass

    # Reconocer primero en español; si langdetect ve inglés, re-reconocer.
    try:
        texto = reconocedor.recognize_google(audio, language='es-ES')
    except sr.UnknownValueError:
        return None, 'es'
    except sr.RequestError as e:
        print(f'STT error: {e}')
        return None, 'es'
    idioma = detectar_idioma(texto)
    if idioma == 'en':
        try:
            texto = reconocedor.recognize_google(audio, language='en-US')
        except Exception:
            pass
    return texto, idioma

def tts_a_bytes(texto, idioma):
    """Texto -> bytes de audio 8-bit SIN signo (silencio=128) a SAMPLE_RATE Hz,
    audio CRUDO listo para el DAC del ESP32 (sin cabecera WAV)."""
    voz = VOZ_ES if idioma == 'es' else VOZ_EN
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        tmp_mp3 = f.name
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        tmp_wav = f.name
    try:
        asyncio.run(edge_tts.Communicate(texto, voz).save(tmp_mp3))
        # Filtros para que el altavoz pequeño se entienda claro y al volumen justo.
        filtros = ('highpass=f=200,lowpass=f=3600,'
                   'dynaudnorm=f=400:g=31,'
                   f'alimiter=limit=0.95,volume={VOLUMEN}')
        subprocess.run(
            [FFMPEG, '-y', '-i', tmp_mp3, '-af', filtros,
             '-ar', str(SAMPLE_RATE), '-ac', '1', '-acodec', 'pcm_u8',
             tmp_wav, '-loglevel', 'quiet'],
            check=True,
        )
        _, datos = wavfile.read(tmp_wav)   # uint8 0..255
        return datos.tobytes()
    finally:
        for tmp in (tmp_mp3, tmp_wav):
            try:
                os.remove(tmp)
            except OSError:
                pass

# ── Sesión con el ESP32 ───────────────────────────────────
def procesar_sesion(sock_mic, sock_spk):
    """Bucle de una conexión activa. Lanza OSError si la conexión se cae."""
    print('Conectado al ESP32. Habla al micrófono...')
    ultimo_turno = None
    while True:
        # 1. Esperar el header (4 bytes). Con settimeout(), recv corta cada 10s
        #    lanzando socket.timeout; lo reintentamos para seguir esperando voz
        #    sin tratarlo como desconexión y permitir Ctrl+C en Windows.
        while True:
            print('Esperando audio del ESP32...')
            try:
                header = sock_mic.recv(4)
                break
            except socket.timeout:
                continue
        if not header or len(header) < 4:
            raise OSError('Conexión de micrófono cerrada por el ESP32')
        tam = struct.unpack('>I', header)[0]
        datos = b''
        while len(datos) < tam:
            chunk = sock_mic.recv(min(4096, tam - len(datos)))
            if not chunk:
                raise OSError('Conexión cortada mientras se recibía audio')
            datos += chunk
        print(f'Audio recibido: {len(datos)} bytes')

        # 2. STT
        texto, idioma = bytes_a_texto(datos)
        if not texto:
            print('No se entendió — enviando silencio al ESP32.')
            sock_spk.sendall(struct.pack('>I', len(SILENCIO)) + SILENCIO)
            continue
        print(f'[{idioma}] Escuché: {texto}')

        # 3. Reset de memoria por inactividad (nuevo visitante)
        if ultimo_turno is not None and (time.time() - ultimo_turno) > RESET_MEMORIA_SEG:
            historial.clear()
            print('Memoria reiniciada (nuevo visitante)')

        # 4. Groq
        print('Consultando Groq...')
        respuesta = preguntar_groq(texto, idioma)
        print(f'Creeper responde: {respuesta}')
        ultimo_turno = time.time()

        # 5. TTS -> enviar al ESP32 (audio crudo 8-bit)
        audio_resp = tts_a_bytes(respuesta, idioma)
        sock_spk.sendall(struct.pack('>I', len(audio_resp)) + audio_resp)
        print(f'Respuesta enviada: {len(audio_resp)} bytes')
        sleep(4)  # espera para evitar solapamiento si el usuario habla rápido

# ── Loop principal con reconexión ─────────────────────────
def main():
    print('CreeperBot (Groq) — cliente del ESP32')
    print(f'ESP32 {ESP32_IP}  | mic:{PORT_MIC}  spk:{PORT_SPK}  | modelo:{GROQ_MODEL}')
    sock_mic = sock_spk = None
    try:
        while True:
            sock_mic = sock_spk = None
            try:
                print(f'Conectando al ESP32 {ESP32_IP}...')
                sock_mic = socket.socket()
                sock_mic.connect((ESP32_IP, PORT_MIC))
                sock_spk = socket.socket()
                sock_spk.connect((ESP32_IP, PORT_SPK))
                sock_mic.settimeout(10.0)
                sock_spk.settimeout(10.0)
                procesar_sesion(sock_mic, sock_spk)
            except (OSError, ConnectionError) as e:
                print(f'[RED] Sin conexión con el ESP32: {e}')
            finally:
                for s in (sock_mic, sock_spk):
                    if s:
                        try:
                            s.close()
                        except OSError:
                            pass
            print(f'[RED] Reintentando en {RECONEXION_SEG} s...')
            time.sleep(RECONEXION_SEG)
    except KeyboardInterrupt:
        print('\nDetenido por el usuario. Cerrando...')
        for s in (sock_mic, sock_spk):
            if s:
                try:
                    s.close()
                except OSError:
                    pass
        sys.exit(0)

if __name__ == '__main__':
    main()
