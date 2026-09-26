import { defineConfig } from '@playwright/test'

/**
 * 用系统已装的 Edge，不下载 Playwright 自带的浏览器（省约 130MB）。
 *
 * `channel: 'msedge'` 必须显式写：devices['Desktop Edge'] 只提供 UA 与视口，
 * 不带 channel，漏了它 Playwright 会去启动自带的 chrome-headless-shell 并报
 * 「Executable doesn't exist」。
 *
 * 前置：先起后端（`fs-serve`）并构建前端（`npm run build`）。
 * 这里刻意不配 webServer 自动起服务——本项目连的是真实工作区、真实模型与真实
 * 事件库，自动起的服务容易让人忘了它在动真数据。
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  reporter: [['list']],
  use: {
    baseURL: process.env.FIRESIGHT_BASE_URL ?? 'http://127.0.0.1:8000',
    channel: 'msedge',
    headless: true,
    viewport: { width: 1600, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
