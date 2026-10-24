"""
bob_falso.py — Bob de mentira para probar bob-cli sin hardware ni red.

Usa el puente real (robot_bob/nucleo/puente_cli.py), así que lo que muestra el CLI es
lo mismo que mostraría con Bob. Lo lanza el CLI con `npm run falso [modo]`:
  demo           (por defecto) un mensaje de cada tipo y un latido cada 2 s
  rafaga [s]     200 líneas por segundo durante s segundos (10 por defecto) y termina
  caida          unas líneas y una excepción sin atrapar (código 1)
"""
import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'robot_bob')))

from nucleo import puente_cli   # noqa: E402

fin = threading.Event()
mic = {'activo': True}


def estado(**cambios) -> None:
    """Lo que en el Bob real manda nucleo/estado_cli.py."""
    puente_cli.emitir({'t': 'estado', **cambios})


def tecla(c: dict) -> None:
    """Atajos del CLI: el Bob real los manda al loop de main.py; acá se muestran y la S
    alterna el micrófono, para ver la barra."""
    print(f"(falso) tecla {c['tecla']}" + (f" · nombre: {c['nombre']}" if c.get('nombre') else ''))
    if c['tecla'] == 's':
        mic['activo'] = not mic['activo']
        estado(mic=mic['activo'])


puente_cli.al_comando('salir', lambda c: fin.set())   # antes de activar (ver T1)
puente_cli.al_comando('tecla', tecla)
puente_cli.activar()

from rich.console import Console   # noqa: E402
import errores                     # noqa: E402

console = Console(force_terminal=True, color_system="truecolor")   # como config.console
modo = sys.argv[1] if len(sys.argv) > 1 else 'demo'


def arranque() -> None:
    """Las mismas líneas que imprime el Bob real al arrancar (copiadas de una corrida).
    Rich se come los [etiquetas] de sus mensajes, por eso algunas empiezan con espacio.
    Hay 13 líneas de librerías: 5 modelos de insightface (2 líneas cada uno), 1 de
    det-size y 2 de pygame."""
    print(' perfil=normal · LLM=rotacion · TTS=piper · clima=True · noticias=True · musica=True')
    print('═' * 60)
    print('  Robot Bob — Sistema Integrado')
    print('═' * 60)
    print('[serial] Conectado WiFi 192.168.0.16:5007')
    print('Conectando al stream de la cámara...')
    print('Esperando primer frame (máx 15 s)...')
    print('[stream] Conectado.')
    print('[tracker] Cámara lista: 320x240')
    print('[memoria] activa. Personas en la base: 23')
    print('[voice_id] WeSpeaker listo (resnet34_LM).')
    for modelo in ('1k3d68', '2d106det', 'det_10g', 'genderage', 'w600k_r50'):
        print("Applied providers: ['CPUExecutionProvider'], with options: {'CPUExecutionProvider': {}}")
        print(f'find model: C:/Users/abdai/.insightface/models/buffalo_l/{modelo}.onnx detection [1, 3, 640, 640] 127.5 128.0')
    print('set det-size: (320, 320)')
    print('[face_id] InsightFace listo (buffalo_l).')
    print(' rotación → gpt-oss-20b → qwen3.8-27b → gpt-oss-120b → ollama')
    print(' LLM=rotación (nube) · TTS=piper · STT=groq · clima=True · noticias=True · musica=True')
    print('pygame 2.6.1 (SDL 2.28.4, Python 3.11.9)')
    print('Hello from the pygame community. https://www.pygame.org/contribute.html')
    print('\nRobot Bob activo. Presiona Q para salir, S para silenciar o activar el micrófono.')
    print(' micrófono continuo + Silero VAD')


def demo() -> None:
    arranque()
    console.print('[voice] Escuchando (8s timeout)...')
    console.print('[bold yellow][wake] "Bob, ¿cómo estás?" → payload: "¿cómo estás?"[/]')
    console.print('[bold green]Bob[/] [dim](FELIZ)[/]: ¡Hola! Estoy muy bien, gracias por preguntar.')
    errores.aviso('CLAUDE', 'No respondió a tiempo: este turno lo contesta Groq.')
    errores.error('CÁMARA', 'No llega video. Revisa que la ESP32-CAM esté encendida.', 'timed out')
    print('[enrolar] nuevo id=3 → Abdair')
    print('un print() a stderr', file=sys.stderr)
    os.write(1, 'INFO: Created TensorFlow Lite XNNPACK delegate (línea cruda en stdout)\n'.encode())
    os.write(2, 'W0000 00:00 librería nativa escribiendo directo a stderr\n'.encode())
    try:
        {}['falta']
    except KeyError:
        traceback.print_exc()
    console.print('[dim]Una línea larga para ver el corte: ' + 'palabra ' * 40 + '[/]')
    estado(fsm='IDLE', mic=True, musica=False, baile=False, ingeniero=False)
    # Una charla de mentira cada 8 s: la barra pasa por cada fase y los turnos se resaltan.
    n = 0
    while not fin.wait(2.0):
        n += 1
        estado(fsm='LISTENING', emocion='ESCUCHANDO')
        if fin.wait(1.5):
            break
        with puente_cli.turno('usuario'):
            console.print(f'[bold cyan]Usuario:[/] ¿Qué hora es? (charla {n})')
        estado(fsm='THINKING', emocion='PENSANDO')
        if fin.wait(2.0):        # acá gira el spinner
            break
        estado(fsm='SPEAKING', emocion='FELIZ')
        with puente_cli.turno('bob'):
            console.print('[bold green]Bob[/] [dim](FELIZ)[/]: Son las cinco y media, ¡hora de bailar!')
        estado(musica=True, baile=n % 2 == 1)
        if fin.wait(1.5):
            break
        estado(fsm='CONVERSATION_IDLE', musica=False, baile=False)
    print('Robot Bob detenido.')


def rafaga(segundos: float) -> None:
    total = int(200 * segundos)
    t0 = time.monotonic()
    for i in range(total):
        print(f'\x1b[2mráfaga {i:05d}\x1b[0m ' + 'x' * 60)
        # Windows duerme de a ~15 ms: un sleep de 5 ms por línea daría 70/s, no 200.
        espera = t0 + (i + 1) / 200 - time.monotonic()
        if espera > 0.02:
            time.sleep(espera)
    print(f'FIN RÁFAGA {total} líneas en {time.monotonic() - t0:.2f} s')


def caida() -> None:
    console.print('[voice] Pensando...')
    errores.error('SERIAL', 'Se perdió COM3: Bob no puede mover los servos.')
    raise RuntimeError('cámara: timed out')


if modo == 'rafaga':
    rafaga(float(sys.argv[2]) if len(sys.argv) > 2 else 10.0)
elif modo == 'caida':
    caida()
else:
    demo()
