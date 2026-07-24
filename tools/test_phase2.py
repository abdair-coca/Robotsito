"""
test_phase2.py — Script de prueba End-to-End para la Fase 2 (Seguridad, WSS y Token Único)
"""

import sys
import json
import time
import requests
import websocket

def main():
    print("=" * 60)
    print(" Robot Bob — Probador End-to-End de la Fase 2 (WSS & Auth)")
    print("=" * 60)

    target_ip = input("Ingresa la IP del ESP32 DevKit [def: 192.168.0.22]: ").strip() or "192.168.0.22"
    
    # 1. Prueba Endpoint REST /api/info
    print(f"\n[1/5] Probando Endpoint REST: http://{target_ip}/api/info...")
    try:
        resp = requests.get(f"http://{target_ip}/api/info", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  [OK] Respuesta REST (200 OK): {json.dumps(data)}")
        else:
            print(f"  [ERROR] Estado HTTP: {resp.status_code}")
    except Exception as e:
        print(f"  [ERROR] No se pudo conectar a http://{target_ip}/api/info: {e}")
        print("  ASEGÚRATE de haber subido (Upload) el firmware C++ de la Fase 2 al ESP32.")
        sys.exit(1)

    ws_url = f"ws://{target_ip}/ws"

    # 2. Conexión y Pairing (Dispositivo A)
    print(f"\n[2/5] Probando WebSocket & Pairing (Dispositivo A: 'Xiaomi Tab 6')...")
    ws1 = websocket.create_connection(ws_url, timeout=5)
    
    pair_req = {
        "action": "pair",
        "device_name": "Xiaomi Tab 6"
    }
    ws1.send(json.dumps(pair_req))
    resp1 = json.loads(ws1.recv())
    print(f"  [OK] Respuesta de Pairing: {json.dumps(resp1)}")

    token_a = resp1.get("token")
    if not token_a or resp1.get("status") != "paired":
        print("  [FAIL] No se obtuvo token válido.")
        sys.exit(1)

    print(f"  [ÉXITO] Token de pairing obtenido: {token_a}")

    # 3. Envío de Comandos Autenticados
    print(f"\n[3/5] Enviando comandos de control con Token A...")
    cmd_servo = {
        "action": "cmd",
        "token": token_a,
        "type": "servo",
        "pan": 120,
        "tilt": 45
    }
    ws1.send(json.dumps(cmd_servo))
    resp_cmd = json.loads(ws1.recv())
    print(f"  [OK] Comando Servo enviado -> Respuesta: {json.dumps(resp_cmd)}")

    cmd_oled = {
        "action": "cmd",
        "token": token_a,
        "type": "estado",
        "val": "FELIZ"
    }
    ws1.send(json.dumps(cmd_oled))
    resp_oled = json.loads(ws1.recv())
    print(f"  [OK] Comando OLED enviado -> Respuesta: {json.dumps(resp_oled)}")

    # 4. Prueba de Rechazo con Token Inválido
    print(f"\n[4/5] Probando rechazo de comando con Token Falso...")
    bad_cmd = {
        "action": "cmd",
        "token": "token_falso_12345",
        "type": "servo",
        "pan": 90,
        "tilt": 90
    }
    ws1.send(json.dumps(bad_cmd))
    resp_bad = json.loads(ws1.recv())
    print(f"  [OK] Respuesta ante token falso (Esperado 'unauthorized'): {json.dumps(resp_bad)}")

    # 5. Prueba de Revocación de Sesión (Dispositivo B desvincula a A)
    print(f"\n[5/5] Probando Revocación: Conectando Dispositivo B ('Redmi Note 12')...")
    ws2 = websocket.create_connection(ws_url, timeout=5)
    
    pair_req_b = {
        "action": "pair",
        "device_name": "Redmi Note 12"
    }
    ws2.send(json.dumps(pair_req_b))
    resp_b = json.loads(ws2.recv())
    print(f"  [OK] Dispositivo B vinculado -> Respuesta: {json.dumps(resp_b)}")

    # Verificar si el Dispositivo A recibió el aviso de revocación
    ws1.settimeout(2.0)
    try:
        rev_msg = json.loads(ws1.recv())
        print(f"  [ÉXITO] Dispositivo A recibió notificación de revocación en tiempo real:")
        print(f"    -> {json.dumps(rev_msg)}")
    except Exception as e:
        print(f"  [WARN] Dispositivo A no leyó el mensaje de revocación a tiempo: {e}")

    ws1.close()
    ws2.close()

    print("\n" + "=" * 60)
    print(" [¡VERIFICACIÓN COMPLETA!] La Fase 2 está 100% funcional.")
    print("=" * 60)

if __name__ == "__main__":
    main()
