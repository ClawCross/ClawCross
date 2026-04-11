import { defineConfig } from '@playwright/test';

const port = process.env.CLAWCROSS_BROWSER_PORT || '51219';
const pythonBin = process.env.CLAWCROSS_TEST_PYTHON || 'python3';

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
