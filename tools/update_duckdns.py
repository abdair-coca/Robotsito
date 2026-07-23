"""
update_duckdns.py — Actualiza las IPs locales de los subdominios de Bob en DuckDNS.
"""

import requests
import sys

TOKEN = "8c12cd1d-1e94-48ea-b2ce-2396fac678aa"

DEV_DOMAIN = "bobcreeper"
DEV_IP = "192.168.0.22"

CAM_DOMAIN = "bobcreeper-cam"
CAM_IP = "192.168.0.21"

def update_domain(domain, ip):
    url = f"https://www.duckdns.org/update?domains={domain}&token={TOKEN}&ip={ip}"
    resp = requests.get(url, timeout=10)
    if "OK" in resp.text:
        print(f"[DuckDNS] {domain}.duckdns.org -> {ip} [ÉXITO]")
        return True
    else:
        print(f"[DuckDNS] {domain}.duckdns.org -> {ip} [ERROR: {resp.text}]")
        return False

def main():
    print("=" * 55)
    print(" Actualizando Subdominios de DuckDNS para Robot Bob")
    print("=" * 55)
    
    ok1 = update_domain(DEV_DOMAIN, DEV_IP)
    ok2 = update_domain(CAM_DOMAIN, CAM_IP)
    
    if ok1 and ok2:
        print("\n¡Ambos subdominios están vinculados a las IPs privadas de tu red!")

if __name__ == "__main__":
    main()
