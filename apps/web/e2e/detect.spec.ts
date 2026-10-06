import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'

/**
 * 识别检测页的端到端验证。
 *
 * 这些正是 headless 截图做不到的事：文件选择框无法触发，所以上传→出结果的
 * 交互路径只能靠驱动真实浏览器来验。
 */

// 前端包是 ESM（package.json 里 "type": "module"），没有 __dirname
const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../../..')
// 取图走新数据集 raw/D-Fire：processed/D-Fire 已于 2026-10-05 删除（内容可由
// data/raw/D-Fire 用 seed 42 精确重建，但不值得为此在测试里依赖一份 2.4GB 的产物）。
// 同一张图在 raw 的 train/images 与旧 processed 的 test 划分下字节一致。
const IMAGE = path.join(ROOT, 'data/raw/D-Fire/train/images/AoF04059.jpg')

test.beforeEach(() => {
  test.skip(!existsSync(IMAGE), `缺少测试图: ${IMAGE}（需先准备 data/raw/D-Fire）`)
})

test('上传真实火情图，出结果对比与检测框账册', async ({ page }) => {
  await page.goto('/detect')

  await expect(page.getByText('把图像拖到这里，或点击选择')).toBeVisible()

  // 文件选择框是 hidden 的，setInputFiles 仍能直接投递
  await page.setInputFiles('input[type=file]', IMAGE)

  // 首次推理要加载权重，给足时间
  const table = page.locator('table.fs-ledger')
  await expect(table).toBeVisible({ timeout: 60_000 })

  // 原图与检测结果并排
  const figures = page.locator('.pair__item')
  await expect(figures).toHaveCount(2)
  await expect(figures.nth(1).locator('img')).toBeVisible()

  // 读数区填上了（目标数不再是占位符）
  await expect(page.locator('.fs-readout').first()).not.toContainText('—')

  // 表里至少有一行，且类别是契约里的中文名
  const rows = table.locator('tbody tr')
  expect(await rows.count()).toBeGreaterThan(0)
  await expect(rows.first()).toContainText(/明火|烟雾/)
})

test('账册表头与数字列对齐（回归）', async ({ page }) => {
  await page.goto('/detect')
  await page.setInputFiles('input[type=file]', IMAGE)

  const table = page.locator('table.fs-ledger')
  await expect(table).toBeVisible({ timeout: 60_000 })

  // 曾经的缺陷：只给 td.is-num 设了右对齐，表头一律左对齐，
  // 于是从第二列起表头贴列左缘、数字贴列右缘，整列错位。
  // 直接断言计算样式——这比截图比对稳定，也精确指向那处修复。
  const thAligns = await table
    .locator('thead th.is-num')
    .evaluateAll((els) => els.map((el) => getComputedStyle(el).textAlign))
  const tdAligns = await table
    .locator('tbody tr')
    .first()
    .locator('td.is-num')
    .evaluateAll((els) => els.map((el) => getComputedStyle(el).textAlign))

  expect(thAligns.length).toBeGreaterThan(0)
  expect(new Set(thAligns)).toEqual(new Set(['right']))
  expect(tdAligns).toEqual(thAligns)
})

test('结果对比图不拉伸变形', async ({ page }) => {
  await page.goto('/detect')
  await page.setInputFiles('input[type=file]', IMAGE)

  const imgs = page.locator('.pair__item img')
  await expect(imgs.nth(1)).toBeVisible({ timeout: 60_000 })

  // 原图与标注图同源同尺寸，显示的宽高比必须一致；
  // 若 CSS 把某一侧压扁，这里会先炸
  const ratios = await imgs.evaluateAll((els) =>
    els.map((el) => {
      const img = el as HTMLImageElement
      return img.naturalWidth / img.naturalHeight
    }),
  )
  expect(ratios).toHaveLength(2)
  expect(Math.abs(ratios[0] - ratios[1])).toBeLessThan(0.01)
})

test('窄屏下布局不塌（回归）', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 900 })
  await page.goto('/detect')

  // 参数栏与取景窗都要在，且不出现横向滚动条
  await expect(page.getByText('取景窗')).toBeVisible()

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(1)
})
