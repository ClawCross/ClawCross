import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import esbuild from 'esbuild';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = path.resolve(__dirname, '..');
const outdir = path.join(projectRoot, 'frontend', 'vendor');

await mkdir(outdir, { recursive: true });

await esbuild.build({
  entryPoints: [path.join(projectRoot, 'tools', 'pretext_global_entry.js')],
  outfile: path.join(outdir, 'pretext.global.js'),
  bundle: true,
  format: 'iife',
  platform: 'browser',
  target: ['es2019'],
  logLevel: 'info',
});
