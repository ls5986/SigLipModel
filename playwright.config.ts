import { defineConfig } from '@playwright/test';
const port = 41735;
export default defineConfig({
  testDir: './frontend/tests',
  use: { baseURL: `http://127.0.0.1:${port}`, headless: true },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${port}`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
