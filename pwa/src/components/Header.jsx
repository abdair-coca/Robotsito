import React from 'react';
import { Bot, Wifi, WifiOff, Camera, Cpu, Settings, ShieldCheck, BatteryCharging } from 'lucide-react';

export default function Header({ isConnected, isCamConnected, pairedDevice, onOpenSettings }) {
  return (
    <header className="glass-panel" style={{ padding: '12px 20px', marginBottom: '20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
        <div style={{ background: 'linear-gradient(135deg, #06b6d4, #8b5cf6)', padding: '10px', borderRadius: '12px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Bot size={28} color="white" />
        </div>
        <div>
          <h1 style={{ fontSize: '1.4rem', fontWeight: 700, letterSpacing: '0.5px', background: 'linear-gradient(to right, #38bdf8, #a78bfa)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            ROBOT BOB
          </h1>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center', gap: '4px' }}>
            <ShieldCheck size={12} color="#10b981" /> PWA Local-First
          </span>
        </div>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
        {/* DevKit Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.8rem', background: 'rgba(15, 23, 42, 0.6)', padding: '6px 12px', borderRadius: '20px', border: '1px solid rgba(255,255,255,0.05)' }}>
          {isConnected ? <Wifi size={14} color="#10b981" /> : <WifiOff size={14} color="#ef4444" />}
          <span>DevKit WSS:</span>
          <strong style={{ color: isConnected ? '#10b981' : '#ef4444' }}>
            {isConnected ? 'Conectado' : 'Desconectado'}
          </strong>
        </div>

        {/* CAM Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.8rem', background: 'rgba(15, 23, 42, 0.6)', padding: '6px 12px', borderRadius: '20px', border: '1px solid rgba(255,255,255,0.05)' }}>
          <Camera size={14} color={isCamConnected ? "#06b6d4" : "#94a3b8"} />
          <span>CAM:</span>
          <strong style={{ color: isCamConnected ? '#06b6d4' : '#94a3b8' }}>
            {isCamConnected ? 'En vivo' : 'Offline'}
          </strong>
        </div>

        {/* Device Pairing */}
        {pairedDevice && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.8rem', background: 'rgba(139, 92, 246, 0.15)', padding: '6px 12px', borderRadius: '20px', border: '1px solid rgba(139, 92, 246, 0.3)' }}>
            <Cpu size={14} color="#a78bfa" />
            <span style={{ color: '#a78bfa', fontWeight: 600 }}>{pairedDevice}</span>
          </div>
        )}

        {/* Settings Button */}
        <button onClick={onOpenSettings} className="glass-panel" style={{ padding: '8px 14px', display: 'flex', alignItems: 'center', gap: '6px', color: 'var(--text-main)', fontSize: '0.85rem' }}>
          <Settings size={16} />
          <span>Ajustes</span>
        </button>
      </div>
    </header>
  );
}
