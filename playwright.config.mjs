import { defineConfig } from '@playwright/test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const port = process.env.CLAWCROSS_BROWSER_PORT || '51219';
const venvPython = process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python';

function resolvePythonBin() {
  if (process.env.CLAWCROSS_TEST_PYTHON) {
    return process.env.CLAWCROSS_TEST_PYTHON;
  }
  const candidates = [
    process.env.VIRTUAL_ENV ? path.join(process.env.VIRTUAL_ENV, venvPython) : '',
    path.join(os.homedir(), '.clawcross', 'venv', venvPython),
    path.join(process.cwd(), '.venv', venvPython),
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return 'python3';
}

const pythonBin = resolvePythonBin();

export default defineConfig({
  testDir: './test/browser',
  timeout: 30_000,
  expect: {
    timeout: 5_000,
  },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `${pythonBin} test/browser/mock_frontend_server.py`,
    url: `http://127.0.0.1:${port}/studio`,
    reuseExistingServer: !process.env.CI,
    env: {
      ...process.env,
      CLAWCROSS_BROWSER_PORT: port,
    },
    timeout: 60_000,
  },
});
