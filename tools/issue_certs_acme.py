"""
issue_certs_acme.py — Generador de certificados SSL Let's Encrypt (DNS-01) para DuckDNS
en Python puro utilizando ACME v2 + cryptography. No requiere permisos de Administrador en Windows.
"""

import os
import sys
import time
import base64
import json
import requests
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from acme import client, messages, challenges, crypto_util
import josepy as jose

DIRECTORY_URL = "https://acme-v02.api.letsencrypt.org/directory"

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode('utf-8').rstrip('=')

def update_duckdns_txt(domain: str, token: str, txt_value: str):
    # domain e.g. bobcreeper
    clean_domain = domain.replace(".duckdns.org", "")
    url = f"https://www.duckdns.org/update?domains={clean_domain}&token={token}&txt={txt_value}"
    resp = requests.get(url, timeout=15)
    if "OK" in resp.text:
        print(f"  [DuckDNS] Registro TXT actualizado para '{clean_domain}': {txt_value[:15]}...")
        return True
    else:
        print(f"  [DuckDNS] Error actualizando TXT: {resp.text}")
        return False

def clear_duckdns_txt(domain: str, token: str):
    clean_domain = domain.replace(".duckdns.org", "")
    url = f"https://www.duckdns.org/update?domains={clean_domain}&token={token}&txt=&clear=true"
    requests.get(url, timeout=10)

def issue_certificate_for_domain(domain: str, duckdns_token: str, email: str, out_dir: Path):
    print(f"\n--- Solicitando certificado para: {domain} ---")
    
    # 1. Generar llave de cuenta ACME y llave de certificado RSA 2048
    acc_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    
    jose_acc_key = jose.JWKRSA(key=acc_key)
    
    net = client.ClientNetwork(jose_acc_key, user_agent="RobotBob-ACME/1.0")
    directory = messages.Directory.from_json(net.get(DIRECTORY_URL).json())
    acme_client = client.ClientV2(directory, net=net)


    # 2. Registrar cuenta ACME
    regr = acme_client.new_account(
        messages.NewRegistration.from_data(
            email=email, terms_of_service_agreed=True
        )
    )
    print(f"  [ACME] Cuenta registrada exitosamente ante Let's Encrypt.")

    # 3. Crear CSR (Certificate Signing Request)
    csr_pem = crypto_util.make_csr(
        cert_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        ),
        [domain]
    )

    # 4. Iniciar Orden de Certificado
    order = acme_client.new_order(csr_pem)
    
    # 5. Resolver Desafío DNS-01
    for authz in order.authorizations:
        for chal in authz.body.challenges:
            if isinstance(chal.chall, challenges.DNS01):
                dns01_chal = chal
                response, validation = dns01_chal.response_and_validation(jose_acc_key)
                
                # Publicar en DuckDNS
                update_duckdns_txt(domain, duckdns_token, validation)
                
                print("  [ACME] Esperando 15s para propagación DNS...")
                time.sleep(15)
                
                # Responder desafío ante Let's Encrypt
                acme_client.answer_challenge(dns01_chal, response)
                
                # Esperar validación
                final_order = acme_client.poll_and_finalize(order)
                
                # Limpiar registro TXT
                clear_duckdns_txt(domain, duckdns_token)
                
                # Guardar Certificado y Llave Privada
                out_dir.mkdir(parents=True, exist_ok=True)
                
                cert_file = out_dir / "fullchain.pem"
                key_file = out_dir / "privkey.pem"
                
                with open(cert_file, "w", encoding="utf-8") as f:
                    f.write(final_order.fullchain_pem)
                    
                with open(key_file, "wb") as f:
                    f.write(cert_key.private_bytes(
                        encoding=serialization.Encoding.PEM,
                        format=serialization.PrivateFormat.TraditionalOpenSSL,
                        encryption_algorithm=serialization.NoEncryption()
                    ))
                    
                print(f"  [ÉXITO] Certificado guardado en: {cert_file}")
                print(f"  [ÉXITO] Llave privada guardada en: {key_file}")
                return True

    print("  [ERROR] No se encontró desafío DNS-01 válido.")
    return False

def main():
    print("=" * 60)
    print(" Robot Bob — Generador de Certificados SSL (Python ACME DNS-01)")
    print("=" * 60)

    token = "8c12cd1d-1e94-48ea-b2ce-2396fac678aa"
    email = "cocaabdair@gmail.com"
    dev_domain = "bobcreeper.duckdns.org"
    cam_domain = "bobcreeper-cam.duckdns.org"

    project_root = Path(__file__).resolve().parent.parent
    dev_out = project_root / "certs" / "dev"
    cam_out = project_root / "certs" / "cam"

    ok1 = issue_certificate_for_domain(dev_domain, token, email, dev_out)
    ok2 = issue_certificate_for_domain(cam_domain, token, email, cam_out)

    if ok1 and ok2:
        print("\n" + "=" * 60)
        print(" [COMPLETADO CON ÉXITO] ¡Ambos certificados SSL están listos!")
        print(f"  DevKit: {dev_out / 'fullchain.pem'}")
        print(f"  CAM:    {cam_out / 'fullchain.pem'}")
        print("=" * 60)

if __name__ == "__main__":
    main()
