import React, { useState } from 'react';
import { Move, Navigation, Smile, ArrowUp, ArrowDown, ArrowLeft, ArrowRight, Square, RotateCcw } from 'lucide-react';

export default function ControlPanels({ onSendCmd }) {
  const [pan, setPan] = useState(90);
  const [tilt, setTilt] = useState(90);
  const [activeEmotion, setActiveEmotion] = useState('Esperando');

  const handleServoChange = (newPan, newTilt) => {
    const clampedPan = Math.max(20, Math.min(160, newPan));
    const clampedTilt = Math.max(40, Math.min(140, newTilt));
    setPan(clampedPan);
    setTilt(clampedTilt);
    onSendCmd('servo', { pan: clampedPan, tilt: clampedTilt });
  };

  const handleMotorCmd = (izq, der) => {
    onSendCmd('motor', { izq, der });
  };

  const handleEmotionChange = (emotion) => {
    setActiveEmotion(emotion);
    onSendCmd('estado', { val: emotion });
  };

  const emotions = [
    { id: 'Esperando', label: 'Neutral', emoji: '👀' },
    { id: 'FELIZ', label: 'Feliz', emoji: '😄' },
    { id: 'SORPRENDIDO', label: 'Sorprendido', emoji: '😲' },
    { id: 'PENSANDO', label: 'Pensando', emoji: '🤔' },
    { id: 'TRISTE', label: 'Triste', emoji: '😢' },
    { id: 'ENOJADO', label: 'Enojado', emoji: '😠' },
  ];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
      
      {/* 1. Control de Servos (Cabeza Pan / Tilt) */}
      <div className="glass-panel" style={{ padding: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Move size={18} color="#06b6d4" />
            <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>Cabeza Pan / Tilt</h3>
          </div>
          <button 
            onClick={() => handleServoChange(90, 90)}
            style={{ padding: '4px 8px', borderRadius: '8px', background: 'rgba(255,255,255,0.05)', color: '#94a3b8', fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '4px' }}
          >
            <RotateCcw size={12} /> Centrar
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
          {/* Pad Direccional para Servos */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 44px)', gap: '8px' }}>
            <div />
            <button onClick={() => handleServoChange(pan, tilt - 10)} className="glass-panel" style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <ArrowUp size={20} color="#06b6d4" />
            </button>
            <div />
            <button onClick={() => handleServoChange(pan + 15, tilt)} className="glass-panel" style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <ArrowLeft size={20} color="#06b6d4" />
            </button>
            <div style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', color: 'var(--text-muted)' }}>
              {pan}°/{tilt}°
            </div>
            <button onClick={() => handleServoChange(pan - 15, tilt)} className="glass-panel" style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <ArrowRight size={20} color="#06b6d4" />
            </button>
            <div />
            <button onClick={() => handleServoChange(pan, tilt + 10)} className="glass-panel" style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <ArrowDown size={20} color="#06b6d4" />
            </button>
            <div />
          </div>
        </div>
      </div>

      {/* 2. Control de Tracción (Motores L298N) */}
      <div className="glass-panel" style={{ padding: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
          <Navigation size={18} color="#8b5cf6" />
          <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>Locomoción (Tracción)</h3>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '12px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 44px)', gap: '8px' }}>
            <div />
            <button onClick={() => handleMotorCmd(60, 60)} className="glass-panel" style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(139, 92, 246, 0.15)' }}>
              <ArrowUp size={20} color="#a78bfa" />
            </button>
            <div />
            <button onClick={() => handleMotorCmd(-50, 50)} className="glass-panel" style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(139, 92, 246, 0.15)' }}>
              <ArrowLeft size={20} color="#a78bfa" />
            </button>
            <button onClick={() => handleMotorCmd(0, 0)} className="glass-panel" style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(239, 68, 68, 0.2)' }}>
              <Square size={16} color="#ef4444" />
            </button>
            <button onClick={() => handleMotorCmd(50, -50)} className="glass-panel" style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(139, 92, 246, 0.15)' }}>
              <ArrowRight size={20} color="#a78bfa" />
            </button>
            <div />
            <button onClick={() => handleMotorCmd(-60, -60)} className="glass-panel" style={{ height: '44px', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(139, 92, 246, 0.15)' }}>
              <ArrowDown size={20} color="#a78bfa" />
            </button>
            <div />
          </div>
        </div>
      </div>

      {/* 3. Selector de Emociones OLED */}
      <div className="glass-panel" style={{ padding: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
          <Smile size={18} color="#10b981" />
          <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>Expresiones OLED</h3>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
          {emotions.map((item) => (
            <button
              key={item.id}
              onClick={() => handleEmotionChange(item.id)}
              style={{
                padding: '8px',
                borderRadius: '10px',
                background: activeEmotion === item.id ? 'rgba(16, 185, 129, 0.25)' : 'rgba(15, 23, 42, 0.6)',
                border: activeEmotion === item.id ? '1px solid #10b981' : '1px solid rgba(255,255,255,0.05)',
                color: 'white',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: '4px',
                fontSize: '0.75rem'
              }}
            >
              <span style={{ fontSize: '1.2rem' }}>{item.emoji}</span>
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      </div>

    </div>
  );
}
