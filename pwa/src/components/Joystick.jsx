import React, { useState, useRef, useEffect } from 'react';

export default function Joystick({ onMove, onRelease, color = '#06b6d4', size = 140 }) {
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef(null);
  const lastSendTime = useRef(0);

  const radius = size / 2;
  const knobRadius = 24;
  const maxDistance = radius - knobRadius;

  const handlePointerDown = (e) => {
    setIsDragging(true);
    updatePosition(e);
  };

  const updatePosition = (e) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const centerX = rect.left + rect.width / 2;
    const centerY = rect.top + rect.height / 2;

    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;

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
    if (now - lastSendTime.current > 50) {
      lastSendTime.current = now;
      if (onMove) onMove(normX, normY);
    }
  };

  const handlePointerMove = (e) => {
    if (!isDragging) return;
    updatePosition(e);
  };

  const handlePointerUp = () => {
    if (!isDragging) return;
    setIsDragging(false);
    setPosition({ x: 0, y: 0 });
    if (onRelease) onRelease();
  };

  useEffect(() => {
    const onMoveGlobal = (e) => isDragging && handlePointerMove(e);
    const onUpGlobal = () => isDragging && handlePointerUp();

    window.addEventListener('mousemove', onMoveGlobal);
    window.addEventListener('mouseup', onUpGlobal);
    window.addEventListener('touchmove', onMoveGlobal);
    window.addEventListener('touchend', onUpGlobal);

    return () => {
      window.removeEventListener('mousemove', onMoveGlobal);
      window.removeEventListener('mouseup', onUpGlobal);
      window.removeEventListener('touchmove', onMoveGlobal);
      window.removeEventListener('touchend', onUpGlobal);
    };
  }, [isDragging]);

  return (
    <div
      ref={containerRef}
      onMouseDown={handlePointerDown}
      onTouchStart={handlePointerDown}
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
        justify: 'center',
        touchAction: 'none',
        cursor: 'grab'
      }}
    >
      {/* Guías centrales en cruz */}
      <div style={{ position: 'absolute', width: '100%', height: '1px', background: `${color}22` }} />
      <div style={{ position: 'absolute', height: '100%', width: '1px', background: `${color}22` }} />

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
