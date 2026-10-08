import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Locator } from '@playwright/test'

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

/**
 * 取一张图的「显示宽高比」与「原始宽高比」。
 *
 * 原来这条回归是比「原图 vs 标注图」两张图的宽高比——那是双栏时代的写法，
 * 现在主位一次只显示一张图，比法换了，但意图没换：图不能被 CSS 压扁或裁切。
 */
async function displayRatio(img: Locator) {
  const box = await img.evaluate((el) => {
    const node = el as HTMLImageElement
    const rect = node.getBoundingClientRect()
    return {
      shown: rect.width / rect.height,
      natural: node.naturalWidth / node.naturalHeight,
    }
  })
  return box
}

test('上传真实火情图，出结果对比与检测框账册', async ({ page }) => {
  await page.goto('/detect')

  await expect(page.getByText('把图像拖到这里，或点击选择')).toBeVisible()

  // 文件选择框是 hidden 的，setInputFiles 仍能直接投递
  await page.setInputFiles('input[type=file]', IMAGE)

  // 首次推理要加载权重，给足时间
  const table = page.locator('table.fs-ledger')
  await expect(table).toBeVisible({ timeout: 60_000 })

  // 图占主位：主区里真的有图
  await expect(page.locator('.stage .pair__item img').first()).toBeVisible()

  // 读数区填上了（目标数不再是占位符）
  await expect(page.locator('.readout').first()).not.toContainText('—')

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

test('结果图不拉伸变形', async ({ page }) => {
  await page.goto('/detect')
  await page.setInputFiles('input[type=file]', IMAGE)

  const overlayImg = page.locator('.overlay img')
  await expect(overlayImg).toBeVisible({ timeout: 60_000 })

  // 显示框的宽高比必须等于原图宽高比。叠加层把 svg 铺在 <img> 的盒子上，
  // 一旦 CSS 把图压扁或裁切，图上的框就会整体错位——所以这条回归盯的是
  // 「图和框用同一套几何」，不是某一张图好不好看。
  const overlayRatio = await displayRatio(overlayImg)
  expect(Math.abs(overlayRatio.shown - overlayRatio.natural)).toBeLessThan(0.01)

  // 后端位图那一侧同样不许变形——它是另一条渲染路径，不该有豁免
  await page.getByRole('radio', { name: '后端标注图' }).click()
  const backendImg = page.locator('.stage img')
  await expect(backendImg).toBeVisible()

  const backendRatio = await displayRatio(backendImg)
  expect(Math.abs(backendRatio.shown - backendRatio.natural)).toBeLessThan(0.01)
})

test('窄屏下布局不塌（回归）', async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 900 })
  await page.goto('/detect')

  // 页头与主位都要在，且不出现横向滚动条
  await expect(page.getByRole('heading', { name: '识别检测' })).toBeVisible()
  await expect(page.locator('.stage')).toBeVisible()

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(overflow).toBeLessThanOrEqual(1)
})

test('默认用前端 SVG 叠加画框，框数与账册一致', async ({ page }) => {
  await page.goto('/detect')
  await page.setInputFiles('input[type=file]', IMAGE)

  const table = page.locator('table.fs-ledger')
  await expect(table).toBeVisible({ timeout: 60_000 })

  // 账本只收够格的框（LEDGER_AT），但图上始终画全：
  // 所以断言的基准是图上的框数，不是账本行数
  const rows = table.locator('tbody tr')
  const boxes = page.locator('.overlay__svg .overlay__box')
  const drawn = await boxes.count()
  expect(drawn).toBeGreaterThan(0)
  expect(await rows.count()).toBeGreaterThan(0)
  expect(await rows.count()).toBeLessThanOrEqual(drawn)

  // 每个框都要带中文类别 + 置信度标签：只画裸矩形比后端那张位图信息更少
  await expect(page.locator('.overlay__tag')).toHaveCount(drawn)
  await expect(page.locator('.overlay__tag').first()).toContainText(/明火|烟雾/)
  await expect(page.locator('.overlay__tag').first()).toContainText(/0\.\d{2}/)

  // 切换器能把主位换成后端位图
  await page.getByRole('radio', { name: '后端标注图' }).click()
  await expect(page.locator('.overlay__svg')).toHaveCount(0)
})

test('账册行与叠加框双向联动（回归）', async ({ page }) => {
  await page.goto('/detect')
  await page.setInputFiles('input[type=file]', IMAGE)

  const table = page.locator('table.fs-ledger')
  await expect(table).toBeVisible({ timeout: 60_000 })

  const rows = table.locator('tbody tr')
  const expected = await rows.count()
  expect(expected).toBeGreaterThan(0)

  const boxes = page.locator('.overlay__svg .overlay__box')
  await expect(boxes).toHaveCount(await boxes.count())

  /** 点亮状态的读数。行与框各有两种可能：正在被指的那一个，
   *  以及「有人指着别的」时被压暗的那些。两个方向都要数得出来。 */
  const litBoxes = () =>
    boxes.evaluateAll((els) => els.filter((el) => el.classList.contains('is-hot')).length)
  const dimBoxes = () =>
    boxes.evaluateAll((els) => els.filter((el) => el.classList.contains('is-dim')).length)
  const litRows = () =>
    rows.evaluateAll((els) => els.filter((el) => el.classList.contains('is-hot')).length)

  // 账册 → 图像：指第一行，第一行自己亮，图上恰好一个框跟着亮，
  // 其余退到暗态。账本行 i 对应 detections 里第 i 个框，所以可以
  // 直接用 nth 断言——不必绕开序号去找 xyxy。
  await rows.first().hover()
  await expect(rows.first()).toHaveClass(/is-hot/)
  expect(await litRows()).toBe(1)
  expect(await litBoxes()).toBe(1)
  if (expected > 1) {
    expect(await dimBoxes()).toBe(expected - 1)
    await expect(boxes.nth(1)).toHaveClass(/is-dim/)
  }

  // 指针移开，两侧都复位——否则会一直留着一个假的选中态
  await page.locator('.stage__title').hover()
  await expect(rows.first()).not.toHaveClass(/is-hot/)
  expect(await litBoxes()).toBe(0)

  // 图像 → 账册：直接指第二个框，账册里对应行才亮
  if (expected > 1) {
    await boxes.nth(1).hover({ force: true })
    expect(await litBoxes()).toBe(1)
    expect(await litRows()).toBe(1)
    await expect(rows.nth(1)).toHaveClass(/is-hot/)
    await expect(rows.first()).not.toHaveClass(/is-hot/)
  }
})
