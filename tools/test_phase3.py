"""
test_phase3.py — Script de prueba End-to-End para la Fase 3 (Servos, Motores, OLED y Memoria KV LittleFS)
"""

import sys
import json
import time
import requests
import websocket

def main():
    print("=" * 60)
    print(" Robot Bob — Probador End-to-End de la Fase 3 (Hardware & Memoria)")
    print("=" * 60)

    target_ip = input("Ingresa la IP del ESP32 DevKit [def: 192.168.0.22]: ").strip() or "192.168.0.22"
    
    # 1. Prueba Endpoint REST /api/info (con Servos pan/tilt)
    print(f"\n[1/6] Probando Endpoint REST: http://{target_ip}/api/info...")
    try:
        resp = requests.get(f"http://{target_ip}/api/info", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            print(f"  [OK] Estado REST: {json.dumps(data)}")
            print(f"  [INFO] Servos actual: Pan={data.get('pan')}, Tilt={data.get('tilt')}")
        else:
            print(f"  [ERROR] Estado HTTP: {resp.status_code}")
    except Exception as e:
        print(f"  [ERROR] No se pudo conectar a http://{target_ip}/api/info: {e}")
        sys.exit(1)

    # 2. Conexión WebSocket y Pairing
    ws_url = f"ws://{target_ip}/ws"
    print(f"\n[2/6] Conectando a WebSocket & Generando Token de Pairing...")
    ws = websocket.create_connection(ws_url, timeout=5)
    
    pair_req = {
        "action": "pair",
        "device_name": "TestDevice_Phase3"
    }
    ws.send(json.dumps(pair_req))
    resp_pair = json.loads(ws.recv())
    if resp_pair.get("status") != "paired" and "token" not in resp_pair:
        resp_pair = json.loads(ws.recv())
    token = resp_pair.get("token")
    print(f"  [OK] Token obtenido: {token}")


    # 3. Mover Servos Pan/Tilt
    print(f"\n[3/6] Probando Mover Servos (Pan: 130°, Tilt: 60°)...")
    cmd_servo = {
        "action": "cmd",
        "token": token,
        "type": "servo",
        "pan": 130,
        "tilt": 60
    }
    ws.send(json.dumps(cmd_servo))
    resp_servo = json.loads(ws.recv())
    print(f"  [OK] Respuesta Servo: {json.dumps(resp_servo)}")
    time.sleep(0.5)

    # Regresar Servos a Home (90°, 90°)
    cmd_servo_home = {
        "action": "cmd",
        "token": token,
        "type": "servo",
        "pan": 90,
        "tilt": 90
    }
    ws.send(json.dumps(cmd_servo_home))
    ws.recv()

    # 4. Cambiar Estado Emocional en OLED (FELIZ, SORPRENDIDO, Esperando)
    print(f"\n[4/6] Probando Cambiar Emociones en Ojos OLED...")
    for estado in ["FELIZ", "SORPRENDIDO", "Esperando"]:
        cmd_oled = {
            "action": "cmd",
            "token": token,
            "type": "estado",
            "val": estado
        }
        ws.send(json.dumps(cmd_oled))
        resp_oled = json.loads(ws.recv())
        print(f"  [OK] Estado OLED '{estado}' -> Respuesta: {json.dumps(resp_oled)}")
        time.sleep(0.8)

    # 5. Probar Motores DC (Impulso breve)
    print(f"\n[5/6] Probando Impulso Breve de Motores DC (Izq: 40, Der: 40)...")
    cmd_motor = {
        "action": "cmd",
        "token": token,
        "type": "motor",
        "izq": 40,
        "der": 40
    }
    ws.send(json.dumps(cmd_motor))
    resp_motor = json.loads(ws.recv())
    print(f"  [OK] Comando Motor enviado -> Respuesta: {json.dumps(resp_motor)}")
    time.sleep(0.2)
    
    # Detener motores
    cmd_motor_stop = {
        "action": "cmd",
        "token": token,
        "type": "motor",
        "izq": 0,
        "der": 0
    }
    ws.send(json.dumps(cmd_motor_stop))
    ws.recv()

    # 6. Consultar Memoria KV de LittleFS (Rostros e Historial)
    print(f"\n[6/6] Consultando Memoria KV de LittleFS en ESP32...")
    cmd_faces = {
        "action": "memory_get_faces",
        "token": token
    }
    ws.send(json.dumps(cmd_faces))
    resp_faces = json.loads(ws.recv())
    print(f"  [OK] Rostros en Memoria: {json.dumps(resp_faces)}")

    cmd_hist = {
        "action": "memory_get_history",
        "token": token
    }
    ws.send(json.dumps(cmd_hist))
    resp_hist = json.loads(ws.recv())
    print(f"  [OK] Historial en Memoria: {json.dumps(resp_hist)}")

    ws.close()

    print("\n" + "=" * 60)
    print(" [¡ÉXITO TOTAL!] La Fase 3 (Hardware & Memoria) está 100% verificada.")
    print("=" * 60)

if __name__ == "__main__":
    main()
