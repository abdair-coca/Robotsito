// Protocolo Bob → CLI (ver robot_bob/nucleo/puente_cli.py).
// stdout de Bob trae NDJSON: un evento por línea. Lo que no sea JSON (una librería
// que escribió directo) y todo stderr se muestran crudos: no se pierde nada.

export type Nivel = 'info' | 'aviso' | 'error';

export type Turno = 'usuario' | 'bob';

/** Lo que cambió del estado de Bob (robot_bob/nucleo/estado_cli.py). */
export interface CambiosEstado {
  fsm?: string;
  emocion?: string;
  mic?: boolean;
  musica?: boolean;
  baile?: boolean;
  ingeniero?: boolean;
}

export type Evento =
  | { t: 'log'; origen: string; nivel: Nivel; ansi: string; turno?: Turno }
  | { t: 'estado'; cambios: CambiosEstado }
  | { t: 'crudo'; de: 'stdout' | 'stderr'; texto: string };

const NIVELES: readonly string[] = ['info', 'aviso', 'error'];
const TURNOS: readonly string[] = ['usuario', 'bob'];

function estadoValido(e: Record<string, unknown>): CambiosEstado {
  const c: CambiosEstado = {};
  for (const k of ['fsm', 'emocion'] as const) if (typeof e[k] === 'string') c[k] = e[k] as string;
  for (const k of ['mic', 'musica', 'baile', 'ingeniero'] as const) {
    if (typeof e[k] === 'boolean') c[k] = e[k] as boolean;
  }
  return c;
}

/** Una línea de stdout o stderr de Bob → evento para mostrar. */
export function parsearLinea(linea: string, de: 'stdout' | 'stderr'): Evento {
  if (de === 'stdout' && linea.startsWith('{')) {
    try {
      const e = JSON.parse(linea);
      if (e && e.t === 'log' && typeof e.ansi === 'string') {
        const log: Extract<Evento, { t: 'log' }> = {
          t: 'log',
          origen: String(e.origen ?? 'bob'),
          nivel: NIVELES.includes(e.nivel) ? e.nivel : 'info',
          ansi: e.ansi,
        };
        if (TURNOS.includes(e.turno)) log.turno = e.turno;
        return log;
      }
      if (e && e.t === 'estado') return { t: 'estado', cambios: estadoValido(e) };
    } catch {
      // no era JSON: va cruda
    }
  }
  return { t: 'crudo', de, texto: linea };
}

/** Arma líneas completas con los chunks de un stream (un chunk puede cortar una
 *  línea o un carácter UTF-8 a la mitad). */
export class Lineas {
  private resto = '';
  private decoder = new TextDecoder('utf-8');

  /** Agrega un chunk y devuelve las líneas que quedaron completas. */
  agregar(chunk: Uint8Array): string[] {
    const partes = (this.resto + this.decoder.decode(chunk, { stream: true })).split('\n');
    this.resto = partes.pop() ?? '';
    return partes.map((l) => (l.endsWith('\r') ? l.slice(0, -1) : l));
  }

  /** Al cerrarse el stream: lo que quedó sin salto de línea. */
  cerrar(): string[] {
    const ultima = this.resto + this.decoder.decode();
    this.resto = '';
    return ultima ? [ultima] : [];
  }
}
