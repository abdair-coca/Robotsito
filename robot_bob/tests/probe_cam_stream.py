"""
probe_cam_stream.py — Una sola conexión sostenida al ESP32-CAM, 10 s. SIN OpenCV.

Replica el patrón de lectura de LectorStream (recv en loop) pero con diagnóstico
crudo: cuenta JPEGs (FFD8..FFD9), bytes/s, y si/ cuándo el CAM corta (RST 10054).

Objetivo: distinguir
  - sostiene 10 s sin cortes      → el bug es la RECONEXIÓN agresiva de LectorStream.
  - corta a los N s / N bytes     → el CAM resetea bajo carga (WiFi/buffer).

Antes: power-cycle del CAM + navegador cerrado (cliente único).

  python tests/probe_cam_stream.py
"""

import socket
import time

HOST, PORT, PATH = '192.168.0.22', 81, '/stream'
DURACION_S = 10.0


def main() -> None:
    print(f'Conexión sostenida a {HOST}:{PORT}{PATH} por {DURACION_S:.0f} s')
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect((HOST, PORT))
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    s.sendall((f'GET {PATH} HTTP/1.1\r\n'
               f'Host: {HOST}:{PORT}\r\n'
               f'User-Agent: robot-bob\r\n'
               f'Accept: */*\r\n'
               f'Connection: keep-alive\r\n\r\n').encode())

    buf = bytearray()
    # saltar cabecera HTTP
    while b'\r\n\r\n' not in buf:
        buf.extend(s.recv(4096))
    del buf[:buf.find(b'\r\n\r\n') + 4]
    print('Cabecera HTTP leída, streaming...')

    t0 = time.monotonic()
    t_stat = t0
    total_bytes = len(buf)
    n_jpeg = 0
    jpeg_en_ventana = 0
    motivo = 'fin normal (10 s)'

    try:
        while time.monotonic() - t0 < DURACION_S:
            try:
                chunk = s.recv(32768)
            except socket.timeout:
                motivo = 'TIMEOUT recv (sin datos 5 s)'
                break
            if not chunk:
                motivo = f'servidor cerró (FIN) a los {time.monotonic()-t0:.1f} s'
                break
            total_bytes += len(chunk)
            buf.extend(chunk)

            # contar JPEGs completos y recortar buffer
            while True:
                ini = buf.find(b'\xff\xd8')
                fin = buf.find(b'\xff\xd9', ini + 2) if ini != -1 else -1
                if ini == -1 or fin == -1:
                    break
                n_jpeg += 1
                jpeg_en_ventana += 1
                del buf[:fin + 2]
            if len(buf) > 2_000_000:
                del buf[:-100_000]

            ahora = time.monotonic()
            if ahora - t_stat >= 1.0:
                kbps = total_bytes / 1024 / (ahora - t0)
                print(f'  t={ahora-t0:4.1f}s  jpeg/s={jpeg_en_ventana:3d}  '
                      f'jpeg_total={n_jpeg:4d}  prom={kbps:6.0f} KB/s')
                jpeg_en_ventana = 0
                t_stat = ahora
    except OSError as e:
        motivo = f'RST/ERROR a los {time.monotonic()-t0:.1f} s tras {total_bytes} bytes: {e}'
    finally:
        try:
            s.close()
        except Exception:
            pass

    dur = time.monotonic() - t0
    print('─' * 60)
    print(f'FIN: {motivo}')
    print(f'  duración: {dur:.1f} s   JPEGs: {n_jpeg}   '
          f'fps medio: {n_jpeg/dur:.1f}   bytes: {total_bytes} '
          f'({total_bytes/1024/max(dur,0.1):.0f} KB/s)')


if __name__ == '__main__':
    main()
