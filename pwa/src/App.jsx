import React, { useState, useEffect, useRef } from 'react';
import Header from './components/Header';
import VideoFeed from './components/VideoFeed';
import ControlPanels from './components/ControlPanels';
import VoiceChat from './components/VoiceChat';
import MemoryPanel from './components/MemoryPanel';
import SettingsModal from './components/SettingsModal';

export default function App() {
  const [isConnected, setIsConnected] = useState(false);
  const [isCamConnected, setIsCamConnected] = useState(true);
  const [pairedDevice, setPairedDevice] = useState(localStorage.getItem('bob_device_name') || 'Redmi Note 12 Pro');
  const [pairedToken, setPairedToken] = useState(localStorage.getItem('bob_token') || '');
  
  const [groqApiKey, setGroqApiKey] = useState(localStorage.getItem('bob_groq_key') || '');
  const [devKitDomain, setDevKitDomain] = useState(localStorage.getItem('bob_devkit_domain') || '192.168.0.22');
  const [camDomain, setCamDomain] = useState(localStorage.getItem('bob_cam_domain') || '192.168.0.21:81');
  const [audioOutput, setAudioOutput] = useState('phone'); // 'phone' | 'robot'
  
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);

  const wsRef = useRef(null);

  // Conexión WebSocket a Bob DevKit
  useEffect(() => {
    let isIP = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/.test(devKitDomain.trim());
    
    // Para IPs (192.168.0.22) usar ws:// siempre. Para dominios DuckDNS usar wss:// si la web es HTTPS.
    let wsProtocol = isIP ? 'ws:' : (window.location.protocol === 'https:' ? 'wss:' : 'ws:');
    
    let wsUrl = devKitDomain.includes('http') || devKitDomain.includes('ws') 
      ? devKitDomain 
      : `${wsProtocol}//${devKitDomain}/ws`;

    console.log('[PWA] Conectando WebSocket a:', wsUrl);
    let socket;
    let isCancelled = false;

    try {
      socket = new WebSocket(wsUrl);
      wsRef.current = socket;

      socket.onopen = () => {
        if (isCancelled) return;
        setIsConnected(true);
        console.log('[WSS] Conectado exitosamente a', wsUrl);

        // Autenticar o vincular
        const storedToken = localStorage.getItem('bob_token');
        if (storedToken) {
          socket.send(JSON.stringify({ action: 'auth', token: storedToken }));
        } else {
          socket.send(JSON.stringify({ action: 'pair', device_name: pairedDevice }));
        }
      };

      socket.onmessage = (event) => {
        if (isCancelled) return;
        try {
          const data = JSON.parse(event.data);
          if (data.status === 'paired' && data.token) {
            setPairedToken(data.token);
            localStorage.setItem('bob_token', data.token);
          } else if (data.type === 'revoked') {
            alert(`Tu sesión fue desvinculada porque se conectó un nuevo dispositivo: ${data.new_device}`);
            setPairedToken('');
            localStorage.removeItem('bob_token');
          }
        } catch (e) {
          console.error(e);
        }
      };

      socket.onclose = () => {
        if (isCancelled) return;
        setIsConnected(false);
        console.log('[WSS] Conexión cerrada');
      };

      socket.onerror = (err) => {
        if (isCancelled) return;
        setIsConnected(false);
        console.warn('[WSS] Error de conexión WebSocket con:', wsUrl);
      };
    } catch (e) {
      console.warn('[WSS] Excepción al crear WebSocket:', e);
    }

    return () => {
      isCancelled = true;
      if (socket) socket.close();
    };
  }, [devKitDomain]);


  // Enviar Comando genérico a Bob (WebSocket con respaldo HTTP REST para la nube)
  const sendCmd = async (type, payload) => {
    const storedToken = localStorage.getItem('bob_token');
    const cmdBody = {
      action: 'cmd',
      token: storedToken,
      type,
      ...payload
    };

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(cmdBody));
    } else {
      // Respaldo por HTTP REST cuando el WebSocket esta bloqueado por la nube (Mixed Content HTTPS -> HTTP/WS)
      try {
        const httpHost = devKitDomain.includes('http') ? devKitDomain : `http://${devKitDomain}`;
        await fetch(`${httpHost}/api/cmd`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(cmdBody)
        });
      } catch (e) {
        console.warn('[REST Fallback] No se pudo enviar comando por HTTP:', e);
      }
    }
  };


  const handlePairNewDevice = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: 'pair', device_name: pairedDevice }));
    }
  };

  // Guardar configuración en localStorage
  useEffect(() => {
    localStorage.setItem('bob_groq_key', groqApiKey);
    localStorage.setItem('bob_devkit_domain', devKitDomain);
    localStorage.setItem('bob_cam_domain', camDomain);
  }, [groqApiKey, devKitDomain, camDomain]);

  const streamUrl = camDomain.includes('http') 
    ? camDomain 
    : `http://${camDomain}/stream`;

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '16px' }}>
      
      {/* Header */}
      <Header
        isConnected={isConnected}
        isCamConnected={isCamConnected}
        pairedDevice={pairedDevice}
        onOpenSettings={() => setIsSettingsOpen(true)}
      />

      {/* Main Grid Layout */}
      <main style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        
        {/* Top Row: Stream Video & Chat Voz */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '20px' }}>
          <VideoFeed
            streamUrl={streamUrl}
            onFaceDetected={(face) => console.log('Face detected:', face)}
          />
          <VoiceChat
            groqApiKey={groqApiKey}
            audioOutput={audioOutput}
            onToggleAudioOutput={() => setAudioOutput(audioOutput === 'phone' ? 'robot' : 'phone')}
          />
        </div>

        {/* Middle Row: Joysticks & Controles */}
        <ControlPanels onSendCmd={sendCmd} />

        {/* Bottom Row: Memoria de Rostros */}
        <MemoryPanel ws={wsRef.current} isConnected={isConnected} />

      </main>

      {/* Modal de Ajustes */}
      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        groqKey={groqApiKey}
        setGroqKey={setGroqApiKey}
        devKitDomain={devKitDomain}
        setDevKitDomain={setDevKitDomain}
        camDomain={camDomain}
        setCamDomain={setCamDomain}
        pairedToken={pairedToken}
        onPairNewDevice={handlePairNewDevice}
      />

    </div>
  );
}
