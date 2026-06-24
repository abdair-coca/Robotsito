"""
discovery.py — auto-localiza las IPs de los ESP32 en la red actual.

Problema que resuelve
---------------------
Las 3 placas (CAM, DevKit de control, DevKit de audio) toman IP por DHCP. Cuando
te cambias de WiFi o el router reasigna, las IPs hardcodeadas en config.py dejan
de servir y el programa no conecta. El firmware de la CAM es Arduino/C inmutable,
así que la solución vive 100% del lado de la laptop: escaneamos la subred y
clasificamos cada placa por el puerto único que expone.

Cómo identifica cada placa
--------------------------
    Puerto 81        -> CAM            (stream MJPEG)
    Puerto 5007      -> control DevKit (servos + OLED, TCP)
    Puerto 5005/5006 -> audio DevKit   (mic / speaker)

La MAC (vía tabla ARP) se usa solo como pista extra: las placas Espressif tienen
OUIs conocidos. No es obligatorio que coincida (algunos clones traen otra MAC).

Uso
---
    python discovery.py            # escanea, imprime tabla, guarda devices.json
    python discovery.py --json     # salida JSON (para scripts)
    python discovery.py --force    # ignora el cache y re-escanea
    python discovery.py --subnet 192.168.1   # forzar otra subred

config.py lee devices.json al importarse y sobrescribe las IPs. Por eso tras
cambiar de WiFi basta con correr `python discovery.py` una vez y arrancar main.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Set

# La consola de Windows suele venir en cp1252; forzamos UTF-8 para que →/—/í no
# salgan como '?'. Mismo truco que main.py. Silencioso si la consola no deja.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── Mapa puerto -> rol ───────────────────────────────────────────────────────
# El rol "audio" se sirve por 5005 (mic) y/o 5006 (speaker); cualquiera basta.
PORT_ROLE: Dict[int, str] = {
    81:   "cam",
    5007: "control",
    5005: "audio",
    5006: "audio",
}
SCAN_PORTS = tuple(PORT_ROLE.keys())

# OUIs Espressif comunes (prefijo MAC en mayúsculas, separador ':'). Pista de
# confianza, NO un filtro duro. Lista parcial pero representativa.
ESPRESSIF_OUI: Set[str] = {
    "24:0A:C4", "24:6F:28", "24:B2:DE", "30:AE:A4", "3C:61:05", "3C:71:BF",
    "48:3F:DA", "54:5A:A6", "5C:CF:7F", "60:01:94", "7C:9E:BD", "7C:DF:A1",
    "84:0D:8E", "84:CC:A8", "84:F3:EB", "8C:AA:B5", "90:38:0C", "94:B9:7E",
    "98:F4:AB", "A0:20:A6", "A0:B7:65", "A4:7B:9D", "A4:CF:12", "AC:0B:FB",
    "AC:67:B2", "B4:E6:2D", "B8:D6:1A", "BC:DD:C2", "C4:4F:33", "C8:2B:96",
    "C8:C9:A3", "CC:50:E3", "D8:A0:1D", "D8:BF:C0", "DC:4F:22", "E0:98:06",
    "E8:9F:6D", "E8:DB:84", "EC:62:60", "EC:E3:34", "EC:FA:BC", "F0:08:D1",
    "F4:CF:A2", "F8:B3:B7", "FC:F5:C4",
}

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "devices.json")
CACHE_MAX_AGE_S = 24 * 3600   # cache más viejo que esto se considera dudoso

# Roles que esperamos resolver y a qué clave de config mapean.
ROLE_CONFIG_KEY = {
    "cam":     "IP_ESPCAM",
    "control": "CONTROL_IP",
    "audio":   "ESP32_IP",
}


# ── Red local ────────────────────────────────────────────────────────────────
def local_ipv4() -> Optional[str]:
    """IP IPv4 de la interfaz activa (sin enviar nada; truco del socket UDP)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))   # no manda paquetes, solo elige ruta
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def subnet_base(ip: str) -> str:
    """'192.168.0.37' -> '192.168.0'  (asume /24, lo normal en casa/feria)."""
    return ip.rsplit(".", 1)[0]


# ── Escaneo de puertos ───────────────────────────────────────────────────────
def _probe(ip: str, port: int, timeout: float) -> bool:
    """True si el TCP connect a ip:port abre (servicio vivo)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def scan_subnet(base: str, ports=SCAN_PORTS, timeout: float = 0.3,
                workers: int = 256) -> Dict[str, Set[int]]:
    """Escanea base.1..254 en los `ports`. Devuelve {ip: {puertos_abiertos}}."""
    targets = [(f"{base}.{h}", p) for h in range(1, 255) for p in ports]
    found: Dict[str, Set[int]] = {}

    def work(t):
        ip, port = t
        return (ip, port) if _probe(ip, port, timeout) else None

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(work, targets):
            if res:
                ip, port = res
                found.setdefault(ip, set()).add(port)
    return found


# ── ARP / MAC (pista Espressif) ──────────────────────────────────────────────
def arp_table() -> Dict[str, str]:
    """Parsea `arp -a` (Windows) -> {ip: MAC_normalizada}. Vacío si falla."""
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table: Dict[str, str] = {}
    # Líneas tipo:  192.168.0.22    a0-b7-65-12-34-56    dynamic
    rx = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})")
    for m in rx.finditer(out):
        ip = m.group(1)
        mac = m.group(2).upper().replace("-", ":")
        table[ip] = mac
    return table


def is_espressif(mac: Optional[str]) -> bool:
    if not mac:
        return False
    return mac[:8] in ESPRESSIF_OUI


# ── Clasificación ────────────────────────────────────────────────────────────
def classify(scan: Dict[str, Set[int]]) -> Dict[str, dict]:
    """
    Asigna roles según puertos. Devuelve {rol: {ip, ports, mac, espressif}}.
    Si dos IPs reclaman el mismo rol, gana la de IP más baja y se avisa.
    """
    arp = arp_table()
    by_role: Dict[str, list] = {}
    for ip, ports in scan.items():
        roles = {PORT_ROLE[p] for p in ports if p in PORT_ROLE}
        for role in roles:
            by_role.setdefault(role, []).append(ip)

    result: Dict[str, dict] = {}
    for role, ips in by_role.items():
        ips_sorted = sorted(ips, key=lambda x: int(x.rsplit(".", 1)[1]))
        if len(ips_sorted) > 1:
            print(f"[discovery] AVISO: {len(ips_sorted)} candidatos para "
                  f"'{role}': {ips_sorted} — uso {ips_sorted[0]}")
        ip = ips_sorted[0]
        mac = arp.get(ip)
        result[role] = {
            "ip": ip,
            "ports": sorted(scan[ip]),
            "mac": mac,
            "espressif": is_espressif(mac),
        }
    return result


# ── Cache ────────────────────────────────────────────────────────────────────
def load_cache() -> Optional[dict]:
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_cache(roles: Dict[str, dict], subnet: str) -> None:
    data = {"ts": time.time(), "subnet": subnet, "roles": roles}
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def cache_ips() -> Dict[str, str]:
    """{config_key: ip} desde el cache, listo para inyectar en config. Vacío si no hay."""
    cache = load_cache()
    if not cache:
        return {}
    out: Dict[str, str] = {}
    for role, info in cache.get("roles", {}).items():
        key = ROLE_CONFIG_KEY.get(role)
        if key and info.get("ip"):
            out[key] = info["ip"]
    return out


# ── Verificación rápida del cache ────────────────────────────────────────────
def verify_cache(timeout: float = 0.3) -> bool:
    """True si toda IP cacheada sigue sirviendo su puerto de rol. Cache vivo => sin re-escaneo."""
    cache = load_cache()
    if not cache or not cache.get("roles"):
        return False
    expected = {v: k for k, v in PORT_ROLE.items()}  # rol -> un puerto (último gana, ok)
    role_port = {"cam": 81, "control": 5007, "audio": 5006}
    for role, info in cache["roles"].items():
        ip = info.get("ip")
        port = role_port.get(role)
        if not ip or port is None:
            continue
        if not _probe(ip, port, timeout):
            # 5006 puede estar cerrado si solo mic; reintenta 5005 para audio
            if role == "audio" and _probe(ip, 5005, timeout):
                continue
            return False
    return True


# ── Orquestación ─────────────────────────────────────────────────────────────
def discover(subnet: Optional[str] = None, timeout: float = 0.3,
             verbose: bool = True) -> Dict[str, dict]:
    """Escaneo completo + clasificación + guardado de cache. Devuelve roles."""
    if subnet is None:
        ip = local_ipv4()
        if not ip:
            raise RuntimeError("No pude determinar la IP local. ¿WiFi conectado?")
        subnet = subnet_base(ip)
    if verbose:
        print(f"[discovery] escaneando {subnet}.1-254 puertos {SCAN_PORTS} ...")
    t0 = time.time()
    scan = scan_subnet(subnet, timeout=timeout)
    roles = classify(scan)
    save_cache(roles, subnet)
    if verbose:
        print(f"[discovery] listo en {time.time()-t0:.1f}s — "
              f"{len(roles)}/3 roles resueltos. Cache: {CACHE_PATH}")
    return roles


def resolve(auto_scan: bool = True, timeout: float = 0.3) -> Dict[str, str]:
    """
    Punto de entrada de alto nivel para código (no CLI).
    1) Si el cache verifica (IPs vivas) -> usa cache (instantáneo).
    2) Si no y auto_scan -> escaneo completo.
    Devuelve {config_key: ip}.
    """
    if verify_cache(timeout):
        return cache_ips()
    if auto_scan:
        roles = discover(timeout=timeout, verbose=False)
        return {ROLE_CONFIG_KEY[r]: info["ip"]
                for r, info in roles.items() if r in ROLE_CONFIG_KEY}
    return cache_ips()


# ── CLI ──────────────────────────────────────────────────────────────────────
def _print_table(roles: Dict[str, dict]) -> None:
    nombre = {"cam": "ESP32-CAM", "control": "DevKit control", "audio": "DevKit audio"}
    print("\n  ROL              IP               PUERTOS        MAC               ESP?")
    print("  " + "-" * 74)
    for role in ("cam", "control", "audio"):
        info = roles.get(role)
        if info:
            mac = info.get("mac") or "(desconocida)"
            esp = "sí" if info.get("espressif") else "—"
            ports = ",".join(str(p) for p in info["ports"])
            print(f"  {nombre[role]:<15}  {info['ip']:<15}  {ports:<13}  {mac:<17} {esp}")
        else:
            print(f"  {nombre[role]:<15}  {'NO ENCONTRADO':<15}  {'-':<13}  {'-':<17} -")
    print()


def main(argv) -> int:
    force = "--force" in argv
    as_json = "--json" in argv
    subnet = None
    if "--subnet" in argv:
        subnet = argv[argv.index("--subnet") + 1]

    if not force and not subnet and verify_cache():
        roles = load_cache()["roles"]
        if not as_json:
            print("[discovery] cache vigente (todas las IPs responden). "
                  "Usa --force para re-escanear.")
    else:
        roles = discover(subnet=subnet, verbose=not as_json)

    if as_json:
        print(json.dumps(roles, indent=2))
    else:
        _print_table(roles)
        faltan = [r for r in ("cam", "control", "audio") if r not in roles]
        if faltan:
            print(f"[discovery] AVISO: sin resolver {faltan}. "
                  "¿Placa apagada o en otra subred? Revisa que estén en el mismo WiFi.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
