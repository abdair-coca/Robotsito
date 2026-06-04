import speech_recognition as sr
from groq import Groq
import edge_tts
import asyncio
import pygame
import tempfile
import os
from config import GROQ_API_KEY

# ========= CONFIG =========

client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """
Eres Creeper, un robot amigable creado por Abdair.
Responde de forma breve y natural.
"""

# ========= TTS =========

async def hablar(texto):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
        archivo = f.name

    try:
        tts = edge_tts.Communicate(
            texto,
            voice="es-MX-DaliaNeural"
        )

        await tts.save(archivo)

        pygame.mixer.init()
        pygame.mixer.music.load(archivo)
        pygame.mixer.music.play()

        while pygame.mixer.music.get_busy():
            pass

    finally:
        try:
            os.remove(archivo)
        except:
            pass

# ========= GROQ =========

def preguntar_groq(texto):

    respuesta = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": texto
            }
        ]
    )

    return respuesta.choices[0].message.content

# ========= STT =========

recognizer = sr.Recognizer()

def escuchar():

    with sr.Microphone() as source:

        print("\n🎤 Habla...")

        recognizer.adjust_for_ambient_noise(
            source,
            duration=1
        )

        audio = recognizer.listen(source)

    try:

        texto = recognizer.recognize_google(
            audio,
            language="es-ES"
        )

        print(f"\nTú: {texto}")

        return texto

    except sr.UnknownValueError:
        print("No entendí.")
        return None

# ========= LOOP =========

print("🤖 Creeper iniciado")
print("Di 'salir' para terminar")

while True:

    texto = escuchar()

    if not texto:
        continue

    if texto.lower() == "salir":
        break

    respuesta = preguntar_groq(texto)

    print(f"\n🤖 Creeper: {respuesta}")

    asyncio.run(
        hablar(respuesta)
    )