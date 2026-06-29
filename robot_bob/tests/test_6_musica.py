"""
test_6_musica.py — Prueba aislada del P6 (control de música Spotify).

Qué prueba (sin red, sin Spotify):
  1. parse_music_command() clasifica bien cada comando y extrae el tema.
  2. Frases de charla normal NO disparan música.
  3. ejecutar() degrada con gracia cuando no hay cliente Spotify (sin credenciales).

Ejecutar:
  cd robot_bob
  python tests/test_6_musica.py
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import music as M

# Forzar la feature ON para testear el parser aunque no haya credenciales en .env.
M.MUSICA_ENABLED = True


def main() -> None:
    print('═' * 60)
    print('  TEST P6 — Música (parser de comandos Spotify)')
    print('═' * 60)
    fallos = 0

    # (texto, accion esperada, query esperada o None)
    casos = [
        ("pon despacito",                          "play",     "despacito"),
        ("reproduce bohemian rhapsody",            "play",     "bohemian rhapsody"),
        ("ponme la canción shape of you",          "play",     "shape of you"),
        ("quiero escuchar a soda stereo",          "play",     "soda stereo"),
        ("toca algo de los beatles",               "play",     "algo de los beatles"),
        ("pon música",                             "resume",   None),
        ("dale play",                              "resume",   None),
        ("pausa la música",                        "pause",    None),
        ("pará la música",                         "pause",    None),
        ("siguiente canción",                      "next",     None),
        ("cambia de canción",                      "next",     None),
        ("canción anterior",                       "prev",     None),
        ("sube el volumen",                        "vol_up",   None),
        ("más fuerte",                             "vol_up",   None),
        ("baja el volumen",                        "vol_down", None),
    ]
    for texto, acc, q in casos:
        intent = M.parse_music_command(texto)
        ok = intent is not None and intent.accion == acc and intent.query == q
        got = f'{intent.accion}/{intent.query!r}' if intent else 'None'
        print(f'  [{"OK" if ok else "FALLO"}] "{texto}" → {got}  (esperado {acc}/{q!r})')
        if not ok:
            fallos += 1

    # Charla normal NO debe disparar música
    print('\n  --- negativos (charla normal) ---')
    for texto in ("hola Bob, ¿cómo estás?", "cuéntame un chiste",
                  "¿qué hora es?", "recuérdame en 5 minutos algo"):
        intent = M.parse_music_command(texto)
        ok = intent is None
        print(f'  [{"OK" if ok else "FALLO"}] "{texto}" → {intent}')
        if not ok:
            fallos += 1

    # Apagada → siempre None
    M.MUSICA_ENABLED = False
    if M.parse_music_command("pon despacito") is not None:
        print('  [FALLO] con MUSICA_ENABLED=False debe devolver None'); fallos += 1
    M.MUSICA_ENABLED = True

    # ejecutar() sin cliente (sin credenciales) → frase amable, sin excepción
    print('\n  --- ejecutar() sin credenciales ---')
    M._sp = None
    msg = M.ejecutar(M.MusicIntent("pause"))
    print(f'  → {msg!r}')
    if not msg or "configurada" not in msg.lower():
        print('  [FALLO] debía avisar que falta configurar Spotify'); fallos += 1

    print('\n' + '═' * 60)
    print(f'  RESULTADO: {"TODO OK" if fallos == 0 else f"{fallos} FALLO(S)"}')
    print('═' * 60)
    sys.exit(1 if fallos else 0)


if __name__ == '__main__':
    main()
