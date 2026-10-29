// bob-cli: lanza a Bob (robot_bob/main.py, por supervisor.py) y muestra todo lo que escribe.
//   npm start              → Bob de verdad
//   npm run falso [modo]   → Bob de mentira (dev/bob_falso.py): demo | rafaga [s] | caida
//   npm run alone          → Bob de verdad sin ESP32 (main.py --alone)

import React from 'react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { render } from 'ink';

import { App } from './app.tsx';
import type { Bob, Lanzamiento } from './bob.ts';

const AQUI = path.dirname(fileURLToPath(import.meta.url));

function lanzamiento(argv: string[]): Lanzamiento {
  const columnas = Math.max(40, (process.stdout.columns ?? 100) - 1);
  const i = argv.indexOf('--falso');
  if (i >= 0) {
    return { script: path.resolve(AQUI, '..', 'dev', 'bob_falso.py'), args: argv.slice(i + 1), columnas };
  }
  // --alone: Bob real sin ESP32. El supervisor se lo pasa a main.py, que lo lee de sys.argv.
  // El supervisor relanza a Bob si se cae o si pide reiniciarse (Fase S, S5).
  const args = argv.includes('--alone') ? ['--alone'] : [];
  return { script: 'supervisor.py', args, columnas };
}

let bob: Bob | null = null;
// Pase lo que pase con el CLI, Bob no queda corriendo suelto.
process.on('exit', () => bob?.matar());

const app = render(<App lanzamiento={lanzamiento(process.argv.slice(2))} registrar={(b) => { bob = b; }} />,
  { exitOnCtrlC: false });
app.waitUntilExit().catch((e: unknown) => {
  // Un error de la vista no debe pasar callado: se muestra y Bob se cierra.
  process.stderr.write(`bob-cli: ${e instanceof Error ? e.stack : String(e)}\n`);
  process.exitCode = 1;
});
