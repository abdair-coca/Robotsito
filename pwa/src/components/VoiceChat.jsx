import React, { useState, useEffect, useRef } from 'react';
import { Mic, MicOff, Volume2, Smartphone, Speaker, Send, Sparkles, MessageSquare } from 'lucide-react';

export default function VoiceChat({ groqApiKey, audioOutput, onToggleAudioOutput }) {
  const [isRecording, setIsRecording] = useState(false);
  const [messages, setMessages] = useState([
    { sender: 'bob', text: '¡Hola! Soy Bob. Estoy listo para conversar contigo.', time: '12:00' }
  ]);
  const [inputMsg, setInputMsg] = useState('');
  const [isThinking, setIsThinking] = useState(false);

  const messagesEndRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendText = async () => {
    if (!inputMsg.trim()) return;

    const userText = inputMsg;
    setInputMsg('');
    const nowStr = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

    setMessages((prev) => [...prev, { sender: 'user', text: userText, time: nowStr }]);
    setIsThinking(true);

    try {
      if (!groqApiKey) {
        throw new Error('Debes configurar tu Groq API Key en los Ajustes.');
      }

      // Llamada directa a Groq API (Llama 3.3 70B Versatile)
      const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${groqApiKey}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          model: 'llama-3.3-70b-versatile',
          messages: [
            { role: 'system', content: 'Eres Bob, un robot interactivo de feria carismático, divertido y alegre. Responde de forma concisa y breve (máximo 2 oraciones).' },
            { role: 'user', content: userText }
          ]
        })
      });

      if (!res.ok) {
        throw new Error(`Error de API Groq: ${res.status}`);
      }

      const data = await res.json();
      const reply = data.choices?.[0]?.message?.content || 'No pude procesar la respuesta.';

      setMessages((prev) => [...prev, { sender: 'bob', text: reply, time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) }]);

      // Si el audio está configurado al teléfono, hablar por Web Speech Synthesis
      if (audioOutput === 'phone' && 'speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(reply);
        utterance.lang = 'es-ES';
        window.speechSynthesis.speak(utterance);
      }
    } catch (err) {
      setMessages((prev) => [...prev, { sender: 'bob', text: `[Error]: ${err.message}`, time: nowStr }]);
    } finally {
      setIsThinking(false);
    }
  };

  const toggleRecording = () => {
    if (!isRecording) {
      setIsRecording(true);
      // Simulación de captura VAD
    } else {
      setIsRecording(false);
    }
  };

  return (
    <div className="glass-panel" style={{ padding: '16px', display: 'flex', flexDirection: 'column', height: '380px' }}>
      {/* Cabecera Voz */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <MessageSquare size={18} color="#06b6d4" />
          <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>Conversación por Voz & IA</h3>
        </div>

        {/* Toggle de Salida de Audio */}
        <button
          onClick={onToggleAudioOutput}
          style={{
            padding: '6px 12px',
            borderRadius: '20px',
            background: audioOutput === 'phone' ? 'rgba(6, 182, 212, 0.2)' : 'rgba(139, 92, 246, 0.2)',
            border: audioOutput === 'phone' ? '1px solid #06b6d4' : '1px solid #8b5cf6',
            color: 'white',
            fontSize: '0.75rem',
            display: 'flex',
            alignItems: 'center',
            gap: '6px'
          }}
        >
          {audioOutput === 'phone' ? <Smartphone size={14} color="#06b6d4" /> : <Speaker size={14} color="#8b5cf6" />}
          <span>Audio: <strong>{audioOutput === 'phone' ? 'Parlante Celular' : 'Parlante Bob'}</strong></span>
        </button>
      </div>

      {/* Mensajes Chat */}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '10px', paddingRight: '4px' }}>
        {messages.map((m, idx) => (
          <div
            key={idx}
            style={{
              alignSelf: m.sender === 'user' ? 'flex-end' : 'flex-start',
              maxWidth: '80%',
              background: m.sender === 'user' ? 'linear-gradient(135deg, #06b6d4, #0284c7)' : 'rgba(15, 23, 42, 0.8)',
              padding: '10px 14px',
              borderRadius: m.sender === 'user' ? '16px 16px 2px 16px' : '16px 16px 16px 2px',
              border: m.sender === 'user' ? 'none' : '1px solid rgba(255,255,255,0.05)',
              fontSize: '0.85rem'
            }}
          >
            <p>{m.text}</p>
            <span style={{ fontSize: '0.65rem', color: 'rgba(255,255,255,0.6)', display: 'block', textAlign: 'right', marginTop: '4px' }}>
              {m.time}
            </span>
          </div>
        ))}
        {isThinking && (
          <div style={{ alignSelf: 'flex-start', background: 'rgba(15, 23, 42, 0.8)', padding: '8px 14px', borderRadius: '16px', fontSize: '0.8rem', color: '#06b6d4', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <Sparkles size={14} className="animate-spin" /> Bob está pensando...
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input y Botón de Micrófono */}
      <div style={{ marginTop: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
        <button
          onClick={toggleRecording}
          style={{
            padding: '10px',
            borderRadius: '50%',
            background: isRecording ? '#ef4444' : 'rgba(6, 182, 212, 0.2)',
            border: isRecording ? 'none' : '1px solid #06b6d4',
            color: 'white',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center'
          }}
        >
          {isRecording ? <MicOff size={20} /> : <Mic size={20} color="#06b6d4" />}
        </button>

        <input
          type="text"
          value={inputMsg}
          onChange={(e) => setInputMsg(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSendText()}
          placeholder="Escribe o habla con Bob..."
          style={{
            flex: 1,
            background: 'rgba(15, 23, 42, 0.6)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '20px',
            padding: '10px 16px',
            color: 'white',
            outline: 'none',
            fontSize: '0.85rem'
          }}
        />

        <button onClick={handleSendText} className="glow-btn" style={{ borderRadius: '50%', padding: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Send size={18} />
        </button>
      </div>
    </div>
  );
}
