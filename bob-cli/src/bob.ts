// Bob como proceso hijo: lo lanza con BOB_CLI=1, convierte su salida en eventos y lo
// cierra. Sin puertos: stdout trae los eventos, stdin lleva los comandos.

import { spawn, spawnSync, type ChildProcess } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { Lineas, parsearLinea, type Evento } from './protocolo.ts';

const AQUI = path.dirname(fileURLToPath(import.meta.url));
export const RAIZ_BOB = path.resolve(AQUI, '..', '..', 'robot_bob');
const PYTHON = path.join(RAIZ_BOB, 'venv311', 'Scripts', 'python.exe');
const ESPERA_SALIR_MS = 10_000;

export interface Lanzamiento {
  /** Script de Python a correr (main.py, o el Bob falso de dev/). */
  script: string;
  args?: string[];
  /** Ancho que usa Rich para cortar líneas (el de la terminal del CLI). */
  columnas: number;
}

export class Bob {
  private proc: ChildProcess | null = null;
  private terminado = false;
  private readonly alEvento: (e: Evento) => void;
  private readonly alSalir: (codigo: number | null) => void;

  constructor(alEvento: (e: Evento) => void, alSalir: (codigo: number | null) => void) {
    this.alEvento = alEvento;
    this.alSalir = alSalir;
  }

  lanzar({ script, args = [], columnas }: Lanzamiento): void {
    const proc = spawn(PYTHON, [script, ...args], {
      cwd: RAIZ_BOB,
      env: {
        ...process.env,
        BOB_CLI: '1',
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
        COLUMNS: String(columnas),
      },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    this.proc = proc;
    for (const [stream, de] of [[proc.stdout!, 'stdout'], [proc.stderr!, 'stderr']] as const) {
      const lineas = new Lineas();
      stream.on('data', (chunk: Buffer) => {
        for (const l of lineas.agregar(chunk)) this.alEvento(parsearLinea(l, de));
      });
      stream.on('end', () => {
        for (const l of lineas.cerrar()) this.alEvento(parsearLinea(l, de));
      });
    }
    proc.stdin!.on('error', () => {});      // Bob ya cerró su stdin: nada que mandar
    proc.on('error', (e) => {
      this.alEvento({ t: 'crudo', de: 'stderr', texto: `No pude lanzar a Bob: ${e.message}` });
      this.terminar(null);
    });
    // 'close' y no 'exit': 'exit' llega antes de que stdout/stderr terminen de vaciarse.
    proc.on('close', (codigo) => this.terminar(codigo));
  }

  /** Manda un comando a Bob ({"cmd": ...}). */
  enviar(cmd: string, extra: Record<string, unknown> = {}): void {
    const stdin = this.proc?.stdin;
    if (stdin && !this.terminado && stdin.writable) stdin.write(JSON.stringify({ cmd, ...extra }) + '\n');
  }

  /** Pide a Bob que cierre limpio; si en 10 s no terminó, lo corta con su árbol.
   *  Cerrar su stdin también lo cierra aunque no atienda "salir" (ver puente_cli). */
  detener(): void {
    if (!this.proc || this.terminado) return;
    this.enviar('salir');
    this.proc.stdin!.end();
    setTimeout(() => this.matar(), ESPERA_SALIR_MS).unref();
  }

  /** Corta a Bob y sus hijos ya (al salir el CLI pase lo que pase). */
  matar(): void {
    if (!this.proc || this.terminado || this.proc.pid === undefined) return;
    spawnSync('taskkill', ['/T', '/F', '/PID', String(this.proc.pid)], { windowsHide: true });
  }

  private terminar(codigo: number | null): void {
    if (this.terminado) return;
    this.terminado = true;
    this.alSalir(codigo);
  }
}
