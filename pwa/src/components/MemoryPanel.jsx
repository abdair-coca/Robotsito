import React, { useState, useEffect } from 'react';
import { Database, UserCheck, Trash2, UserPlus, BrainCircuit } from 'lucide-react';

export default function MemoryPanel({ ws, isConnected }) {
  const [faces, setFaces] = useState([]);
  const [newFaceName, setNewFaceName] = useState('');
  const [activeTab, setActiveTab] = useState('faces');

  useEffect(() => {
    if (isConnected && ws) {
      // Solicitar lista de rostros conocidos desde LittleFS en DevKit
      try {
        ws.send(JSON.stringify({ action: 'memory_get_faces', token: localStorage.getItem('bob_token') }));
      } catch (e) {
        console.error(e);
      }
    }
  }, [isConnected, ws]);

  const handleAddFace = () => {
    if (!newFaceName.trim()) return;
    const fakeEmbedding = new Array(512).fill(0.01);
    
    if (ws && isConnected) {
      ws.send(JSON.stringify({
        action: 'memory_save_face',
        token: localStorage.getItem('bob_token'),
        name: newFaceName,
        embedding: fakeEmbedding,
        age: 25
      }));
    }

    setFaces((prev) => [...prev, { id: String(Date.now()), name: newFaceName, age: 25 }]);
    setNewFaceName('');
  };

  return (
    <div className="glass-panel" style={{ padding: '16px', height: '380px', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Database size={18} color="#06b6d4" />
          <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>Memoria de Bob (LittleFS)</h3>
        </div>

        <div style={{ display: 'flex', gap: '4px', background: 'rgba(15, 23, 42, 0.6)', padding: '2px', borderRadius: '8px' }}>
          <button
            onClick={() => setActiveTab('faces')}
            style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '0.75rem', background: activeTab === 'faces' ? '#06b6d4' : 'transparent', color: activeTab === 'faces' ? 'white' : '#94a3b8' }}
          >
            Rostros
          </button>
          <button
            onClick={() => setActiveTab('history')}
            style={{ padding: '4px 10px', borderRadius: '6px', fontSize: '0.75rem', background: activeTab === 'history' ? '#06b6d4' : 'transparent', color: activeTab === 'history' ? 'white' : '#94a3b8' }}
          >
            Recuerdos
          </button>
        </div>
      </div>

      {activeTab === 'faces' ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
          {/* Formular de Agregar Rostro */}
          <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
            <input
              type="text"
              value={newFaceName}
              onChange={(e) => setNewFaceName(e.target.value)}
              placeholder="Nombre de persona conocida..."
              style={{
                flex: 1,
                background: 'rgba(15, 23, 42, 0.6)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: '8px',
                padding: '8px 12px',
                color: 'white',
                fontSize: '0.8rem',
                outline: 'none'
              }}
            />
            <button onClick={handleAddFace} className="glow-btn" style={{ padding: '8px 14px', fontSize: '0.8rem', display: 'flex', alignItems: 'center', gap: '4px' }}>
              <UserPlus size={14} /> Registrar
            </button>
          </div>

          {/* Lista de Rostros */}
          <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {faces.length === 0 ? (
              <div style={{ textAlign: 'center', padding: '30px', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                <UserCheck size={32} color="#475569" style={{ marginBottom: '8px' }} />
                <p>No hay rostros registrados aún en la memoria flash de Bob.</p>
              </div>
            ) : (
              faces.map((f) => (
                <div key={f.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', background: 'rgba(15, 23, 42, 0.6)', borderRadius: '10px', border: '1px solid rgba(255,255,255,0.05)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <div style={{ background: 'rgba(6, 182, 212, 0.2)', padding: '6px', borderRadius: '50%' }}>
                      <UserCheck size={16} color="#06b6d4" />
                    </div>
                    <div>
                      <strong style={{ fontSize: '0.85rem', display: 'block' }}>{f.name}</strong>
                      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Embedding 512D • MobileFaceNet</span>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      ) : (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          <BrainCircuit size={36} color="#475569" style={{ marginBottom: '8px' }} />
          <p>Historial de temas conversados recordados por Bob.</p>
        </div>
      )}
    </div>
  );
}
