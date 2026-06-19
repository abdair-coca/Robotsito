"""
probe_cam_tcp.py — Probe TCP crudo al ESP32-CAM. SIN OpenCV/MediaPipe.

Objetivo: ver EXACTAMENTE qué responde el CAM a distintas requests HTTP, para
aislar por qué LectorStream recibe RST (WinError 10054) si el navegador sí anda.

Prueba 2 variantes (una conexión nueva cada una, UN intento):
  A) Request idéntica a LectorStream (Host SIN puerto).
  B) Request tipo-navegador (Host CON puerto, headers extra).

Antes de correr: power-cycle del CAM + cerrar el navegador (cliente único).

  python tests/probe_cam_tcp.py
"""

import socket
import time

HOST = '192.168.0.22'
PORT = 81
PATH = '/stream'


def probar(nombre: str, request: bytes) -> None:
    print('─' * 60)
    print(f'[{nombre}] enviando:')
    print(request.decode(errors='replace').rstrip())
    print('─' * 60)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    t0 = time.monotonic()
    try:
        s.connect((HOST, PORT))
        print(f'  connect OK en {(time.monotonic()-t0)*1000:.0f} ms')
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.sendall(request)
        # Leer hasta 3 KB o hasta que corte
        datos = bytearray()
        try:
            while len(datos) < 3072:
                chunk = s.recv(2048)
                if not chunk:
                    print('  servidor cerró (FIN) sin más datos')
                    break
                datos.extend(chunk)
                if b'\r\n\r\n' in datos and len(datos) > 200:
                    break
        except socket.timeout:
            print('  TIMEOUT esperando datos')
        except OSError as e:
            print(f'  ERROR en recv: {e}')

        if datos:
            cabecera = bytes(datos).split(b'\r\n\r\n', 1)[0]
            print('  RESPUESTA (primera línea + headers):')
            for linea in cabecera.split(b'\r\n'):
                print('   |', linea.decode(errors='replace'))
            tiene_jpeg = b'\xff\xd8' in datos
            print(f'  bytes recibidos: {len(datos)}  contiene_JPEG(FFD8): {tiene_jpeg}')
    except OSError as e:
        print(f'  CONNECT/SEND falló: {e}')
    finally:
        try:
            s.close()
        except Exception:
            pass


def main() -> None:
    print('Probe TCP al ESP32-CAM', HOST, PORT, PATH)

    req_a = (f'GET {PATH} HTTP/1.1\r\n'
             f'Host: {HOST}\r\n'
             f'User-Agent: robot-bob\r\n'
             f'Accept: */*\r\n'
             f'Connection: keep-alive\r\n\r\n').encode()

    req_b = (f'GET {PATH} HTTP/1.1\r\n'
             f'Host: {HOST}:{PORT}\r\n'
             f'User-Agent: Mozilla/5.0\r\n'
             f'Accept: image/avif,image/webp,*/*\r\n'
             f'Accept-Encoding: identity\r\n'
             f'Connection: keep-alive\r\n\r\n').encode()

    probar('A — estilo LectorStream', req_a)
    time.sleep(1.0)
    probar('B — estilo navegador', req_b)
    print('─' * 60)
    print('Listo.')


if __name__ == '__main__':
    main()
