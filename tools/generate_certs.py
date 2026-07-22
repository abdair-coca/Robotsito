"""
generate_certs.py — Script automatizado para obtener los certificados SSL de Let's Encrypt (DNS-01)
para Bob usando certbot-dns-duckdns en Python con rutas locales (sin requerir permisos de Administrador).
"""

import os
import sys
import subprocess
from pathlib import Path

def main():
    print("=" * 60)
    print(" Robot Bob — Generador de Certificados SSL (DuckDNS + Let's Encrypt)")
    print("=" * 60)

    token = input("Ingresa tu Token de DuckDNS: ").strip()
    if not token:
        print("[ERROR] El token no puede estar vacío.")
        sys.exit(1)

    email = input("Ingresa tu Email (para Let's Encrypt / notificaciones): ").strip()
    if not email:
        email = "bob.uatf.robot@gmail.com"

    dev_domain = input("Subdominio DevKit [def: bobcreeper]: ").strip() or "bobcreeper"
    cam_domain = input("Subdominio CAM [def: bobcreeper-cam]: ").strip() or "bobcreeper-cam"

    full_dev_domain = f"{dev_domain}.duckdns.org"
    full_cam_domain = f"{cam_domain}.duckdns.org"

    # Directorios locales para certificados
    project_root = Path(__file__).resolve().parent.parent
    certs_dir = project_root / "certs"
    config_dir = certs_dir / "config"
    work_dir = certs_dir / "work"
    logs_dir = certs_dir / "logs"

    config_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Crear duckdns.ini
    ini_path = (project_root / "duckdns.ini").resolve()
    with open(ini_path, "w", encoding="utf-8") as f:
        f.write(f"dns_duckdns_token = {token}\n")

    print(f"\n[1/2] Generando certificado para DevKit: {full_dev_domain}...")
    cmd_dev = [
        sys.executable, "-m", "certbot.main", "certonly",
        "--authenticator", "dns-duckdns",
        "--dns-duckdns-credentials", str(ini_path),
        "--dns-duckdns-propagation-seconds", "30",
        "--config-dir", str(config_dir),
        "--work-dir", str(work_dir),
        "--logs-dir", str(logs_dir),
        "-d", full_dev_domain,
        "--non-interactive", "--agree-tos",
        "-m", email
    ]

    res1 = subprocess.run(cmd_dev)

    print(f"\n[2/2] Generando certificado para CAM: {full_cam_domain}...")
    cmd_cam = [
        sys.executable, "-m", "certbot.main", "certonly",
        "--authenticator", "dns-duckdns",
        "--dns-duckdns-credentials", str(ini_path),
        "--dns-duckdns-propagation-seconds", "30",
        "--config-dir", str(config_dir),
        "--work-dir", str(work_dir),
        "--logs-dir", str(logs_dir),
        "-d", full_cam_domain,
        "--non-interactive", "--agree-tos",
        "-m", email
    ]

    res2 = subprocess.run(cmd_cam)

    # Limpiar archivo de credenciales
    if ini_path.exists():
        os.remove(ini_path)

    if res1.returncode == 0 and res2.returncode == 0:
        print("\n" + "=" * 60)
        print(" [ÉXITO] Certificados SSL generados correctamente:")
        print(f" DevKit certs: {config_dir / 'live' / full_dev_domain}")
        print(f" CAM certs:    {config_dir / 'live' / full_cam_domain}")
        print("=" * 60)
    else:
        print("\n[ATENCIÓN] Revisa los mensajes de error superiores.")

if __name__ == "__main__":
    main()
