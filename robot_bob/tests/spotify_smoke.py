"""
spotify_smoke.py — Prueba en vivo del control de música (P6), SIN voz.

Ejecuta la secuencia de acciones contra tu Spotify real (web player activo) para
verificar que music.ejecutar() controla la reproducción de punta a punta.

  cd robot_bob
  python tests/spotify_smoke.py

Requisitos: .spotify_cache ya creado (corré spotify_auth.py antes) + web player
abierto y reproduciendo en open.spotify.com.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config           # noqa: F401
import music


def paso(intent: 'music.MusicIntent', espera: float) -> None:
    print(f'\n→ {intent.accion}{" «"+intent.query+"»" if intent.query else ""}')
    print('   Bob:', music.ejecutar(intent))
    time.sleep(espera)


def main() -> None:
    if not music.MUSICA_ENABLED:
        print('✗ MUSICA_ENABLED=False — revisá .env'); sys.exit(1)

    print('Secuencia: play → baja vol → siguiente → pausa → resume → pausa.')
    print('Escuchá tu Spotify mientras corre.\n')

    paso(music.MusicIntent('play', query='the way i loved you'), 30)
    paso(music.MusicIntent('vol_down'), 3)
    paso(music.MusicIntent('next'), 5)
    paso(music.MusicIntent('pause'), 2)
    paso(music.MusicIntent('resume'), 4)
    paso(music.MusicIntent('pause'), 0)

    print('\n✓ Secuencia completa. Si oíste los cambios, P6 anda. Ya probás por voz.')


if __name__ == '__main__':
    main()
