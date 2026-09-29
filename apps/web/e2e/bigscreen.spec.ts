import { expect, test } from '@playwright/test'

/** 月度大屏：整页展示、读数与图表渲染、月份回看。 */

test('月度大屏能打开，KPI 与图表都渲染', async ({ page }) => {
  await page.goto('/bigscreen')

  await expect(page.getByText('月度火情统计')).toBeVisible()

  // 大屏是整页展示：常规顶栏导航不该出现
  await expect(page.getByRole('link', { name: '识别检测' })).toHaveCount(0)

  // KPI 行
  await expect(page.getByText('告警总数')).toBeVisible()
  await expect(page.getByText('误报率')).toBeVisible()
  await expect(page.getByText('待复核')).toBeVisible()

  // 图表容器标题（有数据时两张图，没数据时降级成方向性空态——
  // 两种结局都必须有说明文字，不能留白）
  const hasCharts = await page.getByText('近 12 个月告警次数（按类别）').isVisible()
  const hasEmpty = await page.getByText('这个月没有告警记录').isVisible()
  expect(hasCharts || hasEmpty).toBe(true)

  if (hasCharts) {
    // ECharts 是 canvas，真的画了才有内容
    await expect(page.locator('canvas').first()).toBeVisible()
  }
})

test('月份可以回看，到当月后「下一月」停住', async ({ page }) => {
  await page.goto('/bigscreen')

  const title = page.locator('.bs-title')
  const next = page.getByRole('button', { name: '下一月' })
  const before = await title.innerText()

  // 默认停在当月，不能往未来翻
  await expect(next).toBeDisabled()

  await page.getByRole('button', { name: '上一月' }).click()
  await expect(title).not.toHaveText(before)
  await expect(next).toBeEnabled()

  await next.click()
  await expect(title).toHaveText(before)
  await expect(next).toBeDisabled()
})

test('从控制台能进大屏，大屏能回控制台', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: '月度大屏' }).click()
  await expect(page).toHaveURL(/\/bigscreen/)
  await expect(page.getByText('月度火情统计')).toBeVisible()

  await page.getByRole('link', { name: '返回控制台' }).click()
  await expect(page.getByText('当前风险')).toBeVisible()
})
