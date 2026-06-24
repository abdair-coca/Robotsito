# prueba_groq.py
# Verifica la conexión con Groq y prueba la personalidad del Creeper

from groq import Groq
from config import GROQ_API_KEY

cliente = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = """Eres CreeperBot, un robot de feria creado por Abdair.
Responde en máximo 2 oraciones. Sin markdown. A veces di Sssss al inicio.
Responde en el mismo idioma de la pregunta."""

preguntas_prueba = [
    '¿Cómo te llamas?',
    '¿Quién te creó?',
    '¿Qué puedes hacer?',
    'What are you?',
    '¿Vas a explotar?',
]

for pregunta in preguntas_prueba:
    print(f'\nPregunta: {pregunta}')
    respuesta = cliente.chat.completions.create(
        model='llama-3.1-8b-instant',  # rápido, ideal para conversación en tiempo real
        messages=[
            {'role': 'system',  'content': SYSTEM_PROMPT},
            {'role': 'user',    'content': pregunta},
        ],
        max_tokens=200,    # 80 tokens ≈ 2 oraciones
        temperature=0.7,  # 0 = predecible, 1 = creativo
    )
    texto = respuesta.choices[0].message.content
    print(f'Creeper: {texto}')

print('\nPrueba completada')
