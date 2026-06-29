"""
spotify_auth.py — Autorización inicial + chequeo de Spotify (P6).

Corre esto UNA vez tras poner las credenciales en .env:
  cd robot_bob
  python tests/spotify_auth.py

Qué hace:
  1. Dispara el OAuth de Spotify: abre el navegador → autorizás a RobotBob →
     spotipy captura el redirect (127.0.0.1:8888) y cachea el token en .spotify_cache.
  2. Verifica la cuenta (nombre + si es PREMIUM, requisito para controlar playback).
  3. Lista los dispositivos Spotify Connect disponibles (abrí Spotify para que aparezca).

Si todo sale OK, ya podés usar la música por voz con Bob.
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config           # noqa: F401  (carga el .env)
import music


def main() -> None:
    print('═' * 60)
    print('  Autorización Spotify (P6)')
    print('═' * 60)

    if not music.MUSICA_ENABLED:
        print('✗ MUSICA_ENABLED=False — faltan SPOTIFY_CLIENT_ID/SECRET en robot_bob/.env')
        sys.exit(1)

    print('Abriendo navegador para autorizar (aceptá en la página)...')
    sp = music._cliente()
    if sp is None:
        print('✗ No se pudo crear el cliente Spotify (revisá credenciales).')
        sys.exit(1)

    try:
        me = sp.me()
    except Exception as e:
        print(f'✗ Falló la autorización: {e}')
        print('  Revisá que el Redirect URI en el dashboard sea EXACTAMENTE:')
        print(f'    {config.SPOTIFY_REDIRECT_URI}')
        sys.exit(1)

    nombre = me.get('display_name') or me.get('id')
    producto = me.get('product')      # 'premium' | 'free' | 'open'
    print(f'\n✓ Autorizado como: {nombre}')
    print(f'  Tipo de cuenta: {producto}')
    if producto != 'premium':
        print('  ⚠ OJO: la API de playback SOLO funciona con cuenta PREMIUM.')
        print('    Con free vas a poder buscar, pero play/pausa/volumen fallan.')

    print('\nDispositivos Spotify Connect detectados:')
    try:
        devs = sp.devices().get('devices', [])
    except Exception as e:
        devs = []
        print(f'  (no se pudieron listar: {e})')
    if not devs:
        print('  (ninguno) → abrí Spotify en la laptop/celular y reproducí algo una vez.')
    for d in devs:
        activo = ' [ACTIVO]' if d.get('is_active') else ''
        print(f"  • {d.get('name')} ({d.get('type')}) vol={d.get('volume_percent')}%{activo}")

    print('\n' + '═' * 60)
    print('  Listo. Token cacheado en .spotify_cache. Ya podés pedirle música a Bob.')
    print('═' * 60)


if __name__ == '__main__':
    main()
