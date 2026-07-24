import React, { useState, useRef } from 'react';

export default function Joystick({ onMove, onRelease, color = '#06b6d4', size = 140 }) {
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef(null);
  const lastSendTime = useRef(0);

  const radius = size / 2;
  const knobRadius = 24;
  const maxDistance = radius - knobRadius;

  const updatePosition = (clientX, clientY) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;

    let dx = clientX - centerX;
    let dy = clientY - centerY;
    const distance = Math.sqrt(dx * dx + dy * dy);

    if (distance > maxDistance) {
      dx = (dx / distance) * maxDistance;
      dy = (dy / distance) * maxDistance;
    }

    setPosition({ x: dx, y: dy });

    // Normalizado de -1 a +1
    const normX = dx / maxDistance;
    const normY = dy / maxDistance;

    const now = Date.now();
    if (now - lastSendTime.current > 40) {
      lastSendTime.current = now;
      if (onMove) onMove(normX, normY);
    }
  };

  const handlePointerDown = (e) => {
    e.preventDefault();
    setIsDragging(true);
    e.currentTarget.setPointerCapture(e.pointerId);
    updatePosition(e.clientX, e.clientY);
  };

  const handlePointerMove = (e) => {
    if (!isDragging) return;
    e.preventDefault();
    updatePosition(e.clientX, e.clientY);
  };

  const handlePointerUp = (e) => {
    if (!isDragging) return;
    e.preventDefault();
    setIsDragging(false);
    setPosition({ x: 0, y: 0 });
    if (onRelease) onRelease();
  };

  return (
    <div
      ref={containerRef}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
      style={{
        width: `${size}px`,
        height: `${size}px`,
        borderRadius: '50%',
        background: 'rgba(15, 23, 42, 0.8)',
        border: `2px solid ${color}`,
        boxShadow: `0 0 16px ${color}33`,
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        touchAction: 'none',
        userSelect: 'none',
        WebkitUserSelect: 'none',
        cursor: 'grab'
      }}
    >
      {/* Guías centrales en cruz */}
      <div style={{ position: 'absolute', width: '100%', height: '1px', background: `${color}22`, pointerEvents: 'none' }} />
      <div style={{ position: 'absolute', height: '100%', width: '1px', background: `${color}22`, pointerEvents: 'none' }} />

      {/* Palanca / Knob móvil */}
      <div
        style={{
          width: `${knobRadius * 2}px`,
          height: `${knobRadius * 2}px`,
          borderRadius: '50%',
          background: `radial-gradient(circle at 30% 30%, #ffffff, ${color})`,
          boxShadow: `0 0 12px ${color}`,
          position: 'absolute',
          top: `calc(50% - ${knobRadius}px + ${position.y}px)`,
          left: `calc(50% - ${knobRadius}px + ${position.x}px)`,
          transition: isDragging ? 'none' : 'all 0.2s ease-out',
          pointerEvents: 'none'
        }}
      />
    </div>
  );
}
