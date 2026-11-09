import esbuild from 'esbuild';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const root = path.resolve(__dirname, '..');

const entry = path.join(root, 'frontend/oasis-town/src/index.ts');
const out = path.join(root, 'frontend/js/oasis-town.bundle.js');

await esbuild.build({
  entryPoints: [entry],
  outfile: out,
  bundle: true,
  format: 'iife',
  globalName: 'OasisTownBundle',
  platform: 'browser',
  target: ['es2020'],
  sourcemap: false,
  minify: false,
  logLevel: 'info',
  define: {
    'process.env.NODE_ENV': '"production"',
    global: 'globalThis',
  },
  loader: {
    '.ts': 'ts',
  },
});
