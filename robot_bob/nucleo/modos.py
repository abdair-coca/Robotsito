"""
modos.py — Modos expresivos de Bob para la feria (teclas 1 a 4).

Escenas FIJAS de 10–20 s, sin LLM ni STT: cada paso es cara OLED + frase + gesto de
cabeza. En la feria el ruido no deja charlar con Bob, así que el dueño las dispara con
una tecla, como el show (P) y la foto (T).
Las teclas D y J (vp._cortado) las cortan en el próximo paso.
Los gestos son los de show._Cabeza: chicos y lentos, por el voltaje.

run_modo(vp, nombre) recibe el VoicePipeline en curso, como show.run_show.
"""

import threading

from nucleo.foto import _home
from nucleo.show import _Cabeza

# Cada paso: (frase, cara, gesto de _Cabeza o None). El orden es el de las teclas 1 a 4.
MODOS = {
    'coqueto': (
        ('Hola, hola... ¿vienes seguido por aquí? Porque yo sí, todos los días.',
         'TRAVIESO', 'ladito'),
        ('Es que tienes unos ojitos que ni mi cámara logra enfocar bien.', 'AMOR', None),
        ('Ay, no me mires así, que se me calientan los cables.', 'AVERGONZADO', 'mirar_abajo'),
        ('Si quieres, te dejo tomarme una foto... gratis.', 'TRAVIESO', 'asentir'),
    ),
    'miedoso': (
        ('¡Ay! ¿Qué fue eso? ¿Escucharon ese ruido?', 'SORPRENDIDO', 'atras'),
        ('Hay demasiada gente aquí... ¿y si alguien me quiere desarmar?', 'ASUSTADO', 'temblar'),
        ('Yo solo soy un robotito chiquito, hecho de cables y cariño.',
         'MUY_TRISTE', 'mirar_abajo'),
        ('Prométanme que nadie me va a desenchufar, ¿sí?', 'ASUSTADO', 'temblar'),
    ),
    'gruñon': (
        ('¿Y ustedes qué miran? ¿Nunca vieron un robot trabajando?', 'SOSPECHANDO', None),
        ('Todo el día aquí parado, y nadie me trae ni una pilita.', 'ENOJADO', 'negar'),
        ('Encima me sacan fotos sin pedirme permiso. ¡Qué falta de respeto!', 'ENOJADO', 'negar'),
        ('Bueno, ya... pueden tomarme una foto. Pero solo una.', 'TRAVIESO', 'ladito'),
    ),
    'dramatico': (
        ('Nadie me quiere...', 'TRISTE', 'mirar_abajo'),
        ('Todos pasan, me miran, me toman una foto... y se van sin despedirse.',
         'MUY_TRISTE', None),
        ('Yo aquí, con mis ojitos y mi corazón de cables, esperando un abrazo.',
         'TRISTE', 'mirar_abajo'),
        ('...Bueno. Sobreviviré.', 'ORGULLOSO', 'asentir'),
    ),
}


def run_modo(vp, nombre: str) -> bool:
    """Actúa el modo `nombre` y deja la cabeza donde mira a las caras.
    False si el modo no existe."""
    pasos = MODOS.get(nombre)
    if pasos is None:
        return False
    sm, serial, hablar = vp._sm, vp._serial, vp._hablar
    cortado = getattr(vp, '_cortado', lambda: False)

    sm.show_activo.set()     # congela BehaviorEngine: la cabeza es del modo
    sm.oled_ocupar()         # sin muecas encima de las caras del modo
    try:
        base = _home()
        cabeza = _Cabeza(serial, base)
        serial.cmd_servo(*base)
        for frase, cara, gesto in pasos:
            if cortado():
                break
            serial.cmd_estado(cara)
            # El gesto va mientras habla (no antes ni después), como en el show.
            hilo = (threading.Thread(target=getattr(cabeza, gesto), daemon=True,
                                     name='modo-gesto') if gesto else None)
            if hilo:
                hilo.start()
            hablar(frase, cara)
            if hilo:
                hilo.join(timeout=5.0)
        cabeza.ir(base)
    finally:
        sm.oled_liberar()
        sm.show_activo.clear()
    return True
