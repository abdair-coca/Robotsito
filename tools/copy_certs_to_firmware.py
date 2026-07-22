"""
copy_certs_to_firmware.py — Copia los certificados SSL (fullchain.pem y privkey.pem)
a la carpeta data/cert/ de los proyectos C++ del DevKit y de la CAM para su flasheo en LittleFS.
"""

import shutil
from pathlib import Path

def main():
    root = Path(__file__).resolve().parent.parent
    
    dev_src = root / "certs" / "dev"
    cam_src = root / "certs" / "cam"
    
    dev_dst = root / "firmware" / "esp32_devkit_cpp" / "data" / "cert"
    cam_dst = root / "firmware" / "esp32_cam_cpp" / "data" / "cert"
    
    dev_dst.mkdir(parents=True, exist_ok=True)
    cam_dst.mkdir(parents=True, exist_ok=True)
    
    # Copiar certs DevKit
    shutil.copy(dev_src / "fullchain.pem", dev_dst / "cert.pem")
    shutil.copy(dev_src / "privkey.pem", dev_dst / "key.pem")
    print(f"[OK] Certificados copiado a DevKit data: {dev_dst}")
    
    # Copiar certs CAM
    shutil.copy(cam_src / "fullchain.pem", cam_dst / "cert.pem")
    shutil.copy(cam_src / "privkey.pem", cam_dst / "key.pem")
    print(f"[OK] Certificados copiado a CAM data: {cam_dst}")

if __name__ == "__main__":
    main()
