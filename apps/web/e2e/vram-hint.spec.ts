import { expect, test } from '@playwright/test'

/**
 * 显存提示必须跟着设备走，不能留着上一次查询的结果。
 *
 * 回归：imgsz 与 device 各自触发一次 `/api/train/limits`，返回顺序不保证。
 * 没有序号守卫时，慢的那次会覆盖快的结果——实测把设备切成 CPU 后，提示仍然
 * 显示 GPU 的批量上限，而 `overBudget` 会据此禁掉一个 CPU 下完全合法的配置。
 *
 * 这条不需要跑训练，所以单独一个文件：`training.spec.ts` 的 beforeEach 会让
 * 整个文件在不设 FIRESIGHT_E2E_TRAIN 时跳过。
 */

test('切到 CPU 后显存提示消失', async ({ page }) => {
  await page.goto('/training')

  const launcher = page.locator('.launcher')
  await expect(launcher).toBeVisible()

  const hint = launcher.locator('.vram')
  const device = launcher.locator('select').last()

  // GPU：提示上限，并给出标定来源
  await expect(hint).toBeVisible()
  await expect(hint).toContainText('批量上限')
  await expect(hint).toHaveAttribute('title', /batch/)

  // CPU：不做显存校验，提示应当收起
  await device.selectOption('cpu')
  await expect(hint).toBeHidden()

  // 切回 GPU 要能恢复，别只做成单向的
  await device.selectOption('0')
  await expect(hint).toBeVisible()
})
