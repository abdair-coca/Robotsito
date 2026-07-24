import React from 'react';
import { X, Key, Globe, ShieldAlert, Cpu, CheckCircle } from 'lucide-react';

export default function SettingsModal({
  isOpen,
  onClose,
  groqKey,
  setGroqKey,
  devKitDomain,
  setDevKitDomain,
  camDomain,
  setCamDomain,
  pairedToken,
  onPairNewDevice
}) {
  if (!isOpen) return null;

  return (
    <div style={{ position: 'fixed', top: 0, left: 0, right: 0, bottom: 0, background: 'rgba(2, 6, 23, 0.85)', backdropFilter: 'blur(12px)', zIndex: 9999, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px' }}>
      <div className="glass-panel" style={{ width: '100%', maxWidth: '520px', padding: '24px', maxHeight: '90vh', overflowY: 'auto' }}>
        
        {/* Modal Header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
          <h2 style={{ fontSize: '1.2rem', fontWeight: 700, background: 'linear-gradient(to right, #06b6d4, #a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Ajustes & Configuración de Bob
          </h2>
          <button onClick={onClose} style={{ background: 'transparent', color: '#94a3b8' }}>
            <X size={20} />
          </button>
        </div>

        {/* 1. Groq API Key */}
        <div style={{ marginBottom: '20px' }}>
          <label style={{ fontSize: '0.85rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
            <Key size={16} color="#06b6d4" /> Groq API Key (IA Conversacional):
          </label>
          <input
            type="password"
            value={groqKey}
            onChange={(e) => setGroqKey(e.target.value)}
            placeholder="gsk_..."
            style={{ width: '100%', background: 'rgba(15, 23, 42, 0.8)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', padding: '10px 14px', color: 'white', outline: 'none', fontSize: '0.85rem' }}
          />
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginTop: '4px', display: 'block' }}>
            Se guarda localmente en el navegador y permite usar Whisper STT + Llama 3.3 70B de forma gratuita.
          </span>
        </div>

        {/* 2. Subdominios DuckDNS / IPs */}
        <div style={{ marginBottom: '20px' }}>
          <label style={{ fontSize: '0.85rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
            <Globe size={16} color="#8b5cf6" /> Dominio DevKit (WSS / REST):
          </label>
          <input
            type="text"
            value={devKitDomain}
            onChange={(e) => setDevKitDomain(e.target.value)}
            placeholder="bobcreeper.duckdns.org"
            style={{ width: '100%', background: 'rgba(15, 23, 42, 0.8)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', padding: '10px 14px', color: 'white', outline: 'none', fontSize: '0.85rem', marginBottom: '10px' }}
          />

          <label style={{ fontSize: '0.85rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}>
            <Globe size={16} color="#06b6d4" /> Dominio ESP32-CAM (Stream MJPEG):
          </label>
          <input
            type="text"
            value={camDomain}
            onChange={(e) => setCamDomain(e.target.value)}
            placeholder="bobcreeper-cam.duckdns.org"
            style={{ width: '100%', background: 'rgba(15, 23, 42, 0.8)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px', padding: '10px 14px', color: 'white', outline: 'none', fontSize: '0.85rem' }}
          />
        </div>

        {/* 3. Token de Pairing Único */}
        <div style={{ marginBottom: '20px', padding: '12px', background: 'rgba(15, 23, 42, 0.6)', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.05)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
            <span style={{ fontSize: '0.85rem', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Cpu size={16} color="#10b981" /> Token Único de Pairing:
            </span>
            <button onClick={onPairNewDevice} className="glow-btn" style={{ fontSize: '0.75rem', padding: '4px 10px' }}>
              Re-vincular
            </button>
          </div>
          <p style={{ fontSize: '0.75rem', fontFamily: 'monospace', color: '#10b981', wordBreak: 'break-all' }}>
            {pairedToken || 'No vinculado'}
          </p>
        </div>

        {/* 4. Nota de Optimización de Batería en MIUI / HyperOS */}
        <div style={{ padding: '12px', background: 'rgba(245, 158, 11, 0.1)', borderRadius: '10px', border: '1px solid rgba(245, 158, 11, 0.3)' }}>
          <span style={{ fontSize: '0.8rem', fontWeight: 600, color: '#f59e0b', display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
            <ShieldAlert size={16} /> Nota para Xiaomi / MIUI / HyperOS:
          </span>
          <p style={{ fontSize: '0.75rem', color: 'rgba(255,255,255,0.8)', lineHeight: '1.4' }}>
            Para evitar que el sistema suspenda el WebSocket en segundo plano, deshabilita el ahorro de batería para tu navegador en los Ajustes de MIUI / HyperOS (Ahorro de batería -&gt; Sin restricciones).
          </p>
        </div>

        <button onClick={onClose} className="glow-btn" style={{ width: '100%', marginTop: '20px', padding: '12px' }}>
          Guardar & Cerrar
        </button>

      </div>
    </div>
  );
}
