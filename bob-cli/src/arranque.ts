// Panel de arranque: convierte las líneas que Bob imprime al arrancar en un checklist
// (Serial, Cámara, Memoria, Voz, Caras, Cerebro, Audio) y oculta el ruido de las librerías.
// Funciones puras, sin Ink, para poder probarlas con las líneas reales.
//
// Reconoce las líneas por su texto. Si una cambia, esa línea cae al log normal y el
// panel sigue; nada se rompe.

const ESC = '\x1b[';
const VERDE = `${ESC}32m`;
const ROJO = `${ESC}31m`;
const GRIS = `${ESC}2m`;
const NEGRITA = `${ESC}1m`;
const RESET = `${ESC}0m`;

const SECUENCIA = /\x1b\[[0-9;?]*[A-Za-z]/g;
const STREAM = 'stream conectado';

export function quitarAnsi(texto: string): string {
  return texto.replace(SECUENCIA, '');
}

export type Clave = 'serial' | 'camara' | 'memoria' | 'voz' | 'caras' | 'cerebro' | 'audio';

export const PASOS: { clave: Clave; etiqueta: string }[] = [
  { clave: 'serial', etiqueta: 'Serial' },
  { clave: 'camara', etiqueta: 'Cámara' },
  { clave: 'memoria', etiqueta: 'Memoria' },
  { clave: 'voz', etiqueta: 'Voz' },
  { clave: 'caras', etiqueta: 'Caras' },
  { clave: 'cerebro', etiqueta: 'Cerebro' },
  { clave: 'audio', etiqueta: 'Audio' },
];

/** Lo que dice una línea de arranque, ya sin colores. */
export type Clasif =
  | { tipo: 'ignorar' }                                  // banner, progreso, configuración
  | { tipo: 'libreria' }                                 // ruido de insightface y pygame
  | { tipo: 'paso'; clave: Clave; partes: string[] }
  | { tipo: 'camara-stream' }
  | { tipo: 'config'; tts?: string; stt?: string }
  | { tipo: 'activo' }                                   // "Robot Bob activo."
  | { tipo: 'normal' };                                  // cualquier otra línea: va al log

// Mensajes de insightface y pygame: se cuentan y se ocultan.
const LIBRERIAS = [
  /^Applied providers:/,
  /^find model:/,
  /^set det-size:/,
  /^pygame \d/,
  /^Hello from the pygame community/,
];

export interface Arranque {
  abierto: boolean;                                      // false cuando el panel ya se guardó
  datos: Partial<Record<Clave, string[]>>;
  camaraStream: boolean;
  audio: { tts?: string; stt?: string };
  ocultos: number;
}

export function arranqueInicial(): Arranque {
  return { abierto: true, datos: {}, camaraStream: false, audio: {}, ocultos: 0 };
}

/** Clasifica una línea ya sin colores (la limpieza la hace quien llama). */
export function clasificar(linea: string): Clasif {
  const t = linea.trim();
  let m: RegExpExecArray | null;

  if (/^═+$/.test(t) || t === 'Robot Bob — Sistema Integrado'
      || t.startsWith('Conectando al stream') || t.startsWith('Esperando primer frame')) {
    return { tipo: 'ignorar' };
  }
  if (t.includes('TTS=')) {
    // Las dos líneas de configuración: se absorben y el panel toma TTS y STT de ahí.
    return {
      tipo: 'config',
      tts: /\bTTS=(\S+)/.exec(t)?.[1],
      stt: /\bSTT=(\S+)/.exec(t)?.[1],
    };
  }
  if (LIBRERIAS.some((r) => r.test(t))) return { tipo: 'libreria' };
  if (t.startsWith('Robot Bob activo.')) return { tipo: 'activo' };

  if ((m = /^\[serial\] (Simulado.*)$/.exec(t))) {
    return { tipo: 'paso', clave: 'serial', partes: [m[1]] };
  }
  if ((m = /^\[serial\] Conectado (.+)$/.exec(t))) {
    return { tipo: 'paso', clave: 'serial', partes: [m[1]] };
  }
  if (t.startsWith('[stream] Conectado')) return { tipo: 'camara-stream' };
  if ((m = /^\[tracker\] Cámara lista: (\S+)/.exec(t))) {
    return { tipo: 'paso', clave: 'camara', partes: [m[1].replace(/^(\d+)x(\d+)$/, '$1×$2')] };
  }
  if ((m = /^\[memoria\] activa\. Personas en la base: (\d+)/.exec(t))) {
    return { tipo: 'paso', clave: 'memoria', partes: [`${m[1]} personas en la base`] };
  }
  if ((m = /^\[voice_id\] WeSpeaker listo \((.+?)\)/.exec(t))) {
    return { tipo: 'paso', clave: 'voz', partes: [`WeSpeaker ${m[1]}`] };
  }
  if ((m = /^\[face_id\] InsightFace listo \((.+?)\)/.exec(t))) {
    return { tipo: 'paso', clave: 'caras', partes: [`InsightFace ${m[1]}`] };
  }
  if ((m = /(?:^|\s)rotación → (.+)$/.exec(t))) {
    const modelos = m[1].split(' → ').length;
    return { tipo: 'paso', clave: 'cerebro', partes: [`rotación de ${modelos} modelos`] };
  }
  return { tipo: 'normal' };
}

/** Aplica una línea al estado del panel. Devuelve un estado nuevo; si el panel ya se
 *  cerró, no cambia nada. */
export function aplicar(a: Arranque, c: Clasif): Arranque {
  if (!a.abierto) return a;
  switch (c.tipo) {
    case 'libreria':
      return { ...a, ocultos: a.ocultos + 1 };
    case 'paso': {
      const partes = c.clave === 'camara' && a.camaraStream ? [...c.partes, STREAM] : c.partes;
      return { ...a, datos: { ...a.datos, [c.clave]: partes } };
    }
    case 'camara-stream': {
      const previo = a.datos.camara;
      const datos = previo && !previo.includes(STREAM)
        ? { ...a.datos, camara: [...previo, STREAM] }
        : a.datos;
      return { ...a, camaraStream: true, datos };
    }
    case 'config': {
      const audio = { tts: c.tts ?? a.audio.tts, stt: c.stt ?? a.audio.stt };
      const datos = audio.tts
        ? { ...a.datos, audio: [audio.tts, audio.stt].filter((x): x is string => Boolean(x)) }
        : a.datos;
      return { ...a, audio, datos };
    }
    default:
      return a;
  }
}

/** Longitud visible (sin colores). Las letras del panel ocupan una columna cada una. */
function visible(texto: string): number {
  return [...quitarAnsi(texto)].length;
}

export type EstadoPanel = 'en-curso' | 'activo' | 'detenido';

/** El panel como líneas listas para imprimir. Lo mismo sirve en vivo y al guardarlo. */
export function panelTexto(a: Arranque, estado: EstadoPanel): string[] {
  const filas = PASOS.map(({ clave, etiqueta }) => {
    const partes = a.datos[clave];
    return partes
      ? `  ${VERDE}✔${RESET} ${etiqueta.padEnd(10)}${partes.join(' · ')}`
      : `  ${GRIS}○ ${etiqueta.padEnd(10)}esperando…${RESET}`;
  });
  const contenido = [`${NEGRITA}Robot Bob · arranque${RESET}`, '', ...filas];

  const ancho = Math.max(...contenido.map(visible)) + 2;   // un espacio a cada lado
  const caja = [
    `╭${'─'.repeat(ancho)}╮`,
    ...contenido.map((l) => `│ ${l}${' '.repeat(ancho - 1 - visible(l))}│`),
    `╰${'─'.repeat(ancho)}╯`,
  ];

  const pie =
    estado === 'activo' ? `${VERDE}●${RESET} Bob activo  ·  Q salir  ·  S micrófono`
    : estado === 'detenido' ? `${ROJO}✖${RESET} Bob se detuvo antes de quedar activo`
    : `${GRIS}○ arrancando…${RESET}`;

  const lineas = [...caja, pie];
  if (estado !== 'en-curso' && a.ocultos > 0) {
    const plural = a.ocultos === 1 ? 'mensaje' : 'mensajes';
    lineas.push(`${GRIS}  ocultos: ${a.ocultos} ${plural} de librerías${RESET}`);
  }
  return lineas;
}
