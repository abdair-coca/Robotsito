"""
main.py — Entry point del voice chat con ESP32.

Maneja la conexión TCP al ESP32 con reintentos automáticos y delega toda
la lógica conversacional a Chat. Si la conexión se cae, vuelve a conectar
y reanuda; no muere el programa.
"""

from __future__ import annotations

import sys
import time
import socket

from rich.console import Console

from audio_io import AudioIO
from chat import Chat
from config import ESP32_IP, PORT_MIC, PORT_SPK, RECONNECT_S


console = Console()


def main() -> None:
    console.print(
        f"[bold]Creeper VoiceChat[/]  → ESP32 [cyan]{ESP32_IP}[/]  "
        f"mic:{PORT_MIC}  spk:{PORT_SPK}"
    )

    while True:
        io = AudioIO()
        chat = None
        try:
            console.print(f"[dim]Conectando al ESP32 {ESP32_IP}...[/]")
            io.connect()
            console.print("[green]✓ Conectado.[/]")
            chat = Chat(io)
            chat.run()
            # run() retornó normalmente (ej. "adiós") — salimos
            console.print("[dim]Sesión terminada.[/]")
            return
        except KeyboardInterrupt:
            console.print("\n[dim]Detenido por el usuario.[/]")
            return
        except (socket.error, OSError) as e:
            console.print(f"[red]✗ Sin conexión con el ESP32: {e}[/]")
        except Exception as e:
            console.print(f"[red]✗ Error inesperado: {e}[/]")
            import traceback
            traceback.print_exc()
        finally:
            if chat is not None:
                chat.close()
            io.close()

        console.print(f"[dim]Reintentando en {RECONNECT_S}s...[/]")
        try:
            time.sleep(RECONNECT_S)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
