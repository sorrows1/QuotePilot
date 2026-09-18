import { defineConfig } from '@playwright/test'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
export default defineConfig({
  testDir: './e2e', timeout: 60000, retries: 0, workers: 1,
  outputDir: join(tmpdir(), 'quotepilot-browser-evidence'),
  use: { baseURL: 'http://127.0.0.1:5174', viewport: { width: 1280, height: 900 } },
  reporter: 'list',
})
