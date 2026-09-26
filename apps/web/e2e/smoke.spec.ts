import { expect, test } from '@playwright/test'

/** 骨架冒烟：先确认 Playwright 真能驱动系统 Edge 并连上后端。 */

test('仪表盘能打开且读到后端数据', async ({ page }) => {
  await page.goto('/')

  // 顶栏与风险尺是 shell 层的固定元素，页面起来了就一定在
  await expect(page.getByRole('link', { name: 'FireSight' })).toBeVisible()
  await expect(page.getByText('当前风险')).toBeVisible()

  // 归档模型数来自 /api/overview，能出现数字说明后端通了
  await expect(page.getByText('归档模型')).toBeVisible()
})
