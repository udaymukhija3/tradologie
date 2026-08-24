import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: 'http://127.0.0.1:15173',
    channel: 'chromium',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: '/bin/sh ../scripts/run-e2e-backend.sh',
      url: 'http://127.0.0.1:18000/api/health/ready',
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: 'VITE_API_TARGET=http://127.0.0.1:18000 npm run dev -- --host 127.0.0.1 --port 15173',
      url: 'http://127.0.0.1:15173',
      timeout: 60_000,
      reuseExistingServer: false,
    },
  ],
});
