# Red / WiFi — Robot Bob

Cómo la laptop encuentra y habla con los 3 ESP32 sin importar dónde esté el robot.
Resuelve el problema histórico de **IPs DHCP que cambian** al mover de WiFi.

---

## 1. Auto-discovery de IPs — `robot_bob/discovery.py`

Las 3 placas toman IP por DHCP; hardcodearlas rompía la conexión cada vez que
cambiaba la red. `discovery.py` las localiza solas:

- **Escaneo de puertos** del /24 local: clasifica cada placa por su puerto único —
  CAM=`81`, DevKit control=`5007`, DevKit audio=`5005/6`.
- **mDNS**: si el firmware anuncia un hostname `.local`, lo resuelve directo (gana al
  escaneo, sobrevive a cambios de subred). La CAM anuncia **`bob-cam.local`**.
- **MAC vía ARP** (`KNOWN_MAC_ROLES`): el DevKit MicroPython atiende TCP de forma
  **intermitente** (su loop de audio busy-wait 8 kHz bloquea el `accept()`), así que el
  escaneo de puertos lo perdía. ARP responde **siempre** a nivel L2, así que se mapea
  por MAC conocida → rol. Esto lo hace fiable. Si cambias de placa, actualiza la MAC en
  `KNOWN_MAC_ROLES`.

Guarda `robot_bob/devices.json` (gitignored). `config.py` lo lee al importar y
sobrescribe `IP_ESPCAM`/`CONTROL_IP`/`ESP32_IP`. Con `AUTODISCOVER=1` en el entorno,
re-escanea al arrancar si el cache está muerto.

### Uso
```bash
# desde robot_bob/, con venv311 activo
python discovery.py            # escanea, imprime tabla, guarda devices.json
python discovery.py --force    # ignora el cache y re-escanea
python discovery.py --json     # salida JSON
python discovery.py --subnet 192.168.1   # forzar otra subred
```
Flujo tras cambiar de WiFi: `python discovery.py` → `python main.py`.

---

## 2. WiFi portátil — multi-red con fallback

Para llevar el robot a cualquier lugar sin reconfigurar. Cada firmware prueba una
lista de redes en orden y conecta a la que haya.

- **CAM (Arduino):** `WiFiMulti` en el sketch `Documents/Arduino/CameraWebServerBob/`.
  `agregarRedes()` lista las redes (casa, hotspot del celu).
- **DevKit (MicroPython):** `firmware/Esp32/main.py` → `conectar_wifi()` escanea las
  redes en el aire y prueba una lista `REDES` (definida en el `config.py` del device)
  en orden de preferencia. Si no hay `REDES`, cae a `SSID`/`PASSWORD`.

```python
# en el config.py del DevKit (vive en el device, gitignored):
REDES = [
    ("Familia Coca Carlo -5", "..."),   # casa
    ("HotspotCelu", "..."),             # celu
]
STATIC_IP = None   # DHCP; discovery lo encuentra (la IP fija no viaja entre redes)
```

> ⚠️ **ESP32 solo soporta 2.4 GHz.** El hotspot del celu debe estar en 2.4 GHz
> (Android: banda AP → 2.4 GHz; iPhone: "Maximizar compatibilidad").

---

## 3. mDNS en la CAM (`bob-cam.local`)

El sketch hace `MDNS.begin("bob-cam")` + `addService("http", tcp, 80/81)`. Así la CAM
es alcanzable por nombre sin importar la IP. Windows 10/11 resuelve `.local` nativo
(si falla, instalar Bonjour). Verificar:
```bash
python -c "import discovery as d; print(d.mdns_ip('bob-cam.local'))"
```

---

## 4. Cómo hallar la IP de la CAM por serial (si todo falla)

Conectar la CAM por USB → Arduino IDE → Serial Monitor 115200 → reset. Imprime:
`Camera Ready! Use 'http://192.168.0.XX'`. Alternativa: admin del router → clientes
DHCP → buscar la MAC `F8:B3:B7`.

---

## 5. Paridad WiFi == cable (estado y troubleshooting)

Objetivo: que el control por WiFi rinda igual que por USB COM3. El cable es el
**fallback de oro** (`USE_WIFI_SERIAL=False`) y nunca se elimina.

| Síntoma | Causa probable | Acción |
|---|---|---|
| `[stream] No conecta (timed out)` pero ping a la CAM responde | CAM con `accept()` atascado de un run previo (sirve 1 cliente a la vez) | **Power-cycle de la CAM**. Cerrar runs con **Q**, no Ctrl-C. |
| ping a la CAM no responde | CAM caída / no en WiFi / IP cambió | Power-cycle; `python discovery.py --force`. |
| `[serial] No conecta WiFi ...:5007` | DevKit caído / no en la red | Ver IP real (Thonny); `discovery.py`; revisar `REDES` en su config. |
| Stream conecta pero FPS bajo / se corta | Jitter/congestión WiFi 2.4 GHz, señal débil | Red dedicada, canal limpio (1/6/11), ESP32 a <3-5 m del AP. |
| Funciona unos minutos y muere el stream | Reconexión MJPEG bajo WiFi ruidoso | El tracker reconecta solo; si no, power-cycle CAM + mejorar señal. |

Notas:
- La CAM sirve **un solo cliente a la vez**: cerrar navegador/tests antes de `main.py`.
- Firmware del DevKit: power-save WiFi OFF (`pm=PM_NONE`) para estabilidad.
- Audio del robot por WiFi (5005/6) compite con el MJPEG en 2.4 GHz → mantenerlo en la
  laptop (`USE_ROBOT_MIC/SPEAKER=False`) salvo prueba puntual. El control (5007) es
  tráfico mínimo y va por WiFi sin costo.

---

## 6. Criterio de éxito (WiFi == cable)

- [ ] `main.py` arranca por WiFi sin power-cycle manual en 5 arranques seguidos.
- [ ] FPS de seguimiento ≈ cable (~20-25), sin tirones perceptibles.
- [ ] 15 min de demo continua sin caída de stream ni de control.
- [ ] Cerrar y reabrir no deja la CAM atascada.
