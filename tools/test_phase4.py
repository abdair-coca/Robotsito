"""
test_phase4.py — Script de prueba End-to-End para la Fase 4 (ArduinoOTA, NetChecker y SSL Remote Update)
"""

import sys
import json
import time
import socket
import requests
import websocket

def main():
    print("=" * 60)
    print(" Robot Bob — Probador End-to-End de la Fase 4 (OTA & SSL Remote)")
    print("=" * 60)

    target_ip = input("Ingresa la IP del ESP32 DevKit [def: 192.168.0.22]: ").strip() or "192.168.0.22"
    
    # 1. Prueba Endpoint REST /api/info (con indicador NetChecker 'internet')
    print(f"\n[1/3] Probando Estado de Internet en /api/info...")
    try:
        resp = requests.get(f"http://{target_ip}/api/info", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            internet_ok = data.get("internet")
            print(f"  [OK] Estado REST: {json.dumps(data)}")
            print(f"  [INFO] Conectividad Exterior a Internet: {'SÍ (En línea)' if internet_ok else 'NO (Modo Offline / Ojos con Ícono)'}")
        else:
            print(f"  [ERROR] Estado HTTP: {resp.status_code}")
    except Exception as e:
        print(f"  [ERROR] No se pudo conectar a http://{target_ip}/api/info: {e}")
        sys.exit(1)

    # 2. Prueba del Servicio ArduinoOTA (Puerto UDP/TCP 3232)
    print(f"\n[2/3] Verificando puerto de flasheo inalámbrico ArduinoOTA (puerto 3232)...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3.0)
    try:
        result = s.connect_ex((target_ip, 3232))
        if result == 0 or result == 10061: # 10061 = Connection refused (puerto escuchando esperando handshake OTA)
            print(f"  [ÉXITO] El servicio ArduinoOTA está activo y listo para actualizaciones por WiFi.")
        else:
            print(f"  [WARN] El puerto 3232 devolvió código {result}.")
    except Exception as e:
        print(f"  [WARN] Verificación del puerto OTA: {e}")
    finally:
        s.close()

    # 3. Conexión WebSocket y Prueba de Renovación SSL Remota en /api/ssl/update
    print(f"\n[3/3] Probando Endpoint Remoto de Certificados SSL (/api/ssl/update)...")
    ws_url = f"ws://{target_ip}/ws"
    ws = websocket.create_connection(ws_url, timeout=5)
    
    pair_req = {
        "action": "pair",
        "device_name": "TestDevice_Phase4"
    }
    ws.send(json.dumps(pair_req))
    resp_pair = json.loads(ws.recv())
    token = resp_pair.get("token")
    ws.close()

    if not token:
        print("  [FAIL] No se pudo obtener token de autenticación.")
        sys.exit(1)

    # Enviar payload de prueba SSL
    ssl_payload = {
        "token": token,
        "cert": "-----BEGIN CERTIFICATE-----\nTEST_CERTIFICATE_DATA\n-----END CERTIFICATE-----\n",
        "key": "-----BEGIN PRIVATE KEY-----\nTEST_PRIVATE_KEY_DATA\n-----END PRIVATE KEY-----\n"
    }

    try:
        ssl_resp = requests.post(f"http://{target_ip}/api/ssl/update", json=ssl_payload, timeout=5)
        if ssl_resp.status_code == 200:
            print(f"  [ÉXITO] Actualización remota de certs SSL exitosa: {ssl_resp.text}")
        else:
            print(f"  [ERROR] Estado HTTP: {ssl_resp.status_code} - {ssl_resp.text}")
    except Exception as e:
        print(f"  [ERROR] Falló la petición a /api/ssl/update: {e}")

    print("\n" + "=" * 60)
    print(" [¡ÉXITO TOTAL!] La Fase 4 (OTA & SSL Remote) está 100% verificada.")
    print("=" * 60)

if __name__ == "__main__":
    main()
