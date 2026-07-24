#include "wifi_manager.h"
#include <HTTPClient.h>

const byte DNS_PORT = 53;

// HTML del Portal Cautivo para móviles
const char CAPTIVE_PORTAL_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Configurar WiFi — Robot Bob</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; text-align: center; padding: 20px; margin: 0; }
        .card { background: #1e293b; border-radius: 16px; padding: 24px; max-width: 400px; margin: 20px auto; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }
        h2 { color: #38bdf8; margin-bottom: 8px; }
        p { color: #94a3b8; font-size: 14px; }
        input, select { width: 100%; padding: 12px; margin: 10px 0; border-radius: 8px; border: 1px solid #334155; background: #0f172a; color: #fff; box-sizing: border-box; font-size: 16px; }
        button { width: 100%; padding: 14px; background: #0284c7; color: white; border: none; border-radius: 8px; font-size: 16px; font-weight: bold; cursor: pointer; margin-top: 10px; }
        button:hover { background: #0369a1; }
    </style>
</head>
<body>
    <div class="card">
        <h2>🤖 Robot Bob</h2>
        <p>Conecta a Bob a la red WiFi local</p>
        <form action="/save" method="POST">
            <label style="text-align:left; display:block; font-size:12px; color:#94a3b8;">Nombre de Red (SSID):</label>
            <input type="text" name="ssid" placeholder="Nombre de la red WiFi" required>
            <label style="text-align:left; display:block; font-size:12px; color:#94a3b8;">Contraseña:</label>
            <input type="password" name="password" placeholder="Contraseña de WiFi">
            <button type="submit">Guardar y Conectar</button>
        </form>
    </div>
</body>
</html>
)rawliteral";

BobWiFiManager::BobWiFiManager() : _server(nullptr), _connected(false), _isSoftAP(false) {}

void BobWiFiManager::begin(AsyncWebServer* server, const char* duckdns_token, const char* duckdns_subdomain) {
    _server = server;
    _duckdnsToken = duckdns_token;
    _duckdnsSubdomain = duckdns_subdomain;
    
    _prefs.begin("bob_wifi", false);

    Serial.println("[WiFiManager] Intentando conectar a redes conocidas...");
    if (!connectSavedNetworks()) {
        Serial.println("[WiFiManager] No se pudo conectar a ninguna red conocida. Iniciando Portal Cautivo SoftAP...");
        startCaptivePortal();
    } else {
        Serial.println("[WiFiManager] Conexión WiFi establecida exitosamente.");
        updateDuckDNS();
    }
}

bool BobWiFiManager::connectSavedNetworks() {
    int count = _prefs.getInt("count", 0);
    if (count <= 0) return false;

    WiFi.mode(WIFI_STA);
    
    for (int i = 0; i < count; i++) {
        String keySsid = "ssid_" + String(i);
        String keyPass = "pass_" + String(i);
        
        String ssid = _prefs.getString(keySsid.c_str(), "");
        String pass = _prefs.getString(keyPass.c_str(), "");
        
        if (ssid.length() == 0) continue;

        Serial.printf("[WiFiManager] Intentando [%d/%d]: %s ...\n", i + 1, count, ssid.c_str());
        WiFi.begin(ssid.c_str(), pass.c_str());

        int attempts = 0;
        while (WiFi.status() != WL_CONNECTED && attempts < 20) {
            delay(500);
            Serial.print(".");
            attempts++;
        }
        Serial.println();

        if (WiFi.status() == WL_CONNECTED) {
            _connected = true;
            _isSoftAP = false;
            Serial.printf("[WiFiManager] Conectado a '%s' — IP Local: %s\n", ssid.c_str(), WiFi.localIP().toString().c_str());
            return true;
        }
    }
    return false;
}

void BobWiFiManager::startCaptivePortal() {
    _isSoftAP = true;
    _connected = false;
    
    WiFi.mode(WIFI_AP);
    WiFi.softAP("Bob-Setup", nullptr);
    
    IPAddress apIP = WiFi.softAPIP();
    Serial.printf("[WiFiManager] SoftAP 'Bob-Setup' activo en IP: %s\n", apIP.toString().c_str());

    // Iniciar servidor DNS redirigiendo todo al portal captivo
    _dnsServer.start(DNS_PORT, "*", apIP);
    
    setupPortalRoutes();
}

void BobWiFiManager::setupPortalRoutes() {
    if (!_server) return;

    // Portal Cautivo HTTP
    _server->on("/", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", CAPTIVE_PORTAL_HTML);
    });

    _server->on("/generate_204", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", CAPTIVE_PORTAL_HTML);
    });

    _server->on("/redirect", HTTP_GET, [](AsyncWebServerRequest *request) {
        request->send(200, "text/html", CAPTIVE_PORTAL_HTML);
    });

    // Guardar credenciales recibidas
    _server->on("/save", HTTP_POST, [this](AsyncWebServerRequest *request) {
        String ssid = "";
        String pass = "";
        
        if (request->hasParam("ssid", true)) {
            ssid = request->getParam("ssid", true)->value();
        }
        if (request->hasParam("password", true)) {
            pass = request->getParam("password", true)->value();
        }

        if (ssid.length() > 0) {
            saveNetwork(ssid, pass);
            String response = "<html><body style='font-family:sans-serif; background:#0f172a; color:#fff; text-align:center; padding:50px;'>"
                              "<h2>Red Guardada</h2><p>Bob se esta reiniciando para conectarse a <b>" + ssid + "</b>...</p></body></html>";
            request->send(200, "text/html", response);
            delay(2000);
            ESP.restart();
        } else {
            request->send(400, "text/plain", "SSID Invalido");
        }
    });

    // Redirección por defecto para cautivo
    _server->onNotFound([](AsyncWebServerRequest *request) {
        request->send(200, "text/html", CAPTIVE_PORTAL_HTML);
    });
}

void BobWiFiManager::loop() {
    if (_isSoftAP) {
        _dnsServer.processNextRequest();
    }
}

bool BobWiFiManager::isConnected() {
    return _connected && (WiFi.status() == WL_CONNECTED);
}

String BobWiFiManager::getLocalIP() {
    if (_isSoftAP) return WiFi.softAPIP().toString();
    return WiFi.localIP().toString();
}

bool BobWiFiManager::isSoftAP() {
    return _isSoftAP;
}

void BobWiFiManager::saveNetwork(const String& ssid, const String& password) {
    int count = _prefs.getInt("count", 0);
    
    // Verificar si ya existe
    for (int i = 0; i < count; i++) {
        if (_prefs.getString(("ssid_" + String(i)).c_str(), "") == ssid) {
            _prefs.putString(("pass_" + String(i)).c_str(), password);
            Serial.printf("[WiFiManager] Red '%s' actualizada en NVS.\n", ssid.c_str());
            return;
        }
    }
    
    // Guardar nueva red
    _prefs.putString(("ssid_" + String(count)).c_str(), ssid);
    _prefs.putString(("pass_" + String(count)).c_str(), password);
    _prefs.putInt("count", count + 1);
    Serial.printf("[WiFiManager] Red '%s' guardada en NVS (total: %d).\n", ssid.c_str(), count + 1);
}

void BobWiFiManager::forgetNetwork(const String& ssid) {
    int count = _prefs.getInt("count", 0);
    int newCount = 0;
    
    for (int i = 0; i < count; i++) {
        String s = _prefs.getString(("ssid_" + String(i)).c_str(), "");
        String p = _prefs.getString(("pass_" + String(i)).c_str(), "");
        
        if (s != ssid && s.length() > 0) {
            _prefs.putString(("ssid_" + String(newCount)).c_str(), s);
            _prefs.putString(("pass_" + String(newCount)).c_str(), p);
            newCount++;
        }
    }
    _prefs.putInt("count", newCount);
    Serial.printf("[WiFiManager] Red '%s' olvidada. Restantes: %d\n", ssid.c_str(), newCount);
}

String BobWiFiManager::getSavedNetworksJson() {
    StaticJsonDocument<512> doc;
    JsonArray array = doc.to<JsonArray>();
    
    int count = _prefs.getInt("count", 0);
    for (int i = 0; i < count; i++) {
        String s = _prefs.getString(("ssid_" + String(i)).c_str(), "");
        if (s.length() > 0) {
            array.add(s);
        }
    }
    String json;
    serializeJson(doc, json);
    return json;
}

void BobWiFiManager::updateDuckDNS() {
    if (!isConnected() || _duckdnsToken.length() == 0 || _duckdnsSubdomain.length() == 0) return;
    
    HTTPClient http;
    String url = "http://www.duckdns.org/update?domains=" + _duckdnsSubdomain + 
                 "&token=" + _duckdnsToken + 
                 "&ip=" + getLocalIP();
                 
    Serial.printf("[DuckDNS] Actualizando %s.duckdns.org -> %s ...\n", _duckdnsSubdomain.c_str(), getLocalIP().c_str());
    http.begin(url);
    int httpCode = http.GET();
    if (httpCode > 0) {
        String payload = http.getString();
        Serial.printf("[DuckDNS] Respuesta (%d): %s\n", httpCode, payload.c_str());
    } else {
        Serial.printf("[DuckDNS] Error de conexion: %s\n", http.errorToString(httpCode).c_str());
    }
    http.end();
}
