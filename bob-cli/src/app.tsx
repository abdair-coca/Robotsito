// Vista del CLI: arriba el historial de lo que Bob escribe, abajo una línea de estado.
// El historial va con write() de Ink (queda en el scroll de la terminal y no se
// acumula en memoria); las líneas se juntan en lotes para aguantar ráfagas.

import React, { useEffect, useRef, useState } from 'react';
import { Box, Text, useApp, useInput, useStdin, useStdout } from 'ink';

import { Bob, type Lanzamiento } from './bob.ts';
import type { Evento } from './protocolo.ts';

const LOTE_MS = 50;
const GRIS = '\x1b[2m';
const NORMAL = '\x1b[0m';

type Estado =
  | { fase: 'corriendo' }
  | { fase: 'cerrando' }
  | { fase: 'caido'; codigo: number | null };

function aTexto(e: Evento): string {
  if (e.t === 'log') return e.ansi;
  return e.de === 'stderr' ? `${GRIS}${e.texto}${NORMAL}` : e.texto;
}

export function App({ lanzamiento, registrar }: {
  lanzamiento: Lanzamiento;
  registrar: (bob: Bob) => void;
}) {
  const { exit } = useApp();
  const { write } = useStdout();
  const { isRawModeSupported } = useStdin();
  const [estado, setEstado] = useState<Estado>({ fase: 'corriendo' });
  const bob = useRef<Bob | null>(null);
  const cola = useRef<string[]>([]);
  const pedido = useRef(false);   // el usuario pidió cerrar (no es una caída)

  const volcar = () => {
    if (cola.current.length === 0) return;
    write(cola.current.join('\n') + '\n');
    cola.current = [];
  };

  useEffect(() => {
    const b = new Bob(
      (e) => cola.current.push(aTexto(e)),
      (codigo) => {
        volcar();
        // Cierre normal o pedido (con Ctrl+C, Python sale con 0xC000013A): el CLI se
        // va con Bob. Si no, es una caída: queda abierto para leer qué pasó.
        if (codigo === 0 || pedido.current) exit();
        else setEstado({ fase: 'caido', codigo });
      },
    );
    bob.current = b;
    registrar(b);
    b.lanzar(lanzamiento);
    const t = setInterval(volcar, LOTE_MS);
    return () => clearInterval(t);
  }, []);

  useInput((input, key) => {
    if (!(key.ctrl && input === 'c')) return;
    if (estado.fase === 'caido') return exit();
    if (estado.fase === 'cerrando') return bob.current?.matar();   // 2º Ctrl+C: ya
    pedido.current = true;
    setEstado({ fase: 'cerrando' });
    bob.current?.detener();
  }, { isActive: isRawModeSupported === true });   // sin terminal Ink da undefined, y
                                                    // useInput solo se apaga con false

  return (
    <Box borderStyle="round" borderColor={estado.fase === 'caido' ? 'red' : 'gray'} paddingX={1}>
      {estado.fase === 'corriendo' && (
        <Text><Text color="green">●</Text> Bob corriendo <Text dimColor>· Ctrl+C para salir</Text></Text>
      )}
      {estado.fase === 'cerrando' && (
        <Text><Text color="yellow">●</Text> Cerrando a Bob… <Text dimColor>· Ctrl+C otra vez para cortarlo ya</Text></Text>
      )}
      {estado.fase === 'caido' && (
        <Text><Text color="red">●</Text> Bob se detuvo (código {String(estado.codigo)}) <Text dimColor>· Ctrl+C para salir</Text></Text>
      )}
    </Box>
  );
}
