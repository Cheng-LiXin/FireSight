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
  // 顶栏右侧那个「月度大屏」入口，固定挑这一处，测试不对「入口有几个」作假设。
  await page.getByRole('banner').getByRole('link', { name: '月度大屏' }).click()
  await expect(page).toHaveURL(/\/bigscreen/)
  await expect(page.getByText('月度火情统计')).toBeVisible()

  await page.getByRole('link', { name: '返回控制台' }).click()
  await expect(page.getByText('当前风险')).toBeVisible()
})

/** 打桩月度统计。返回体形状照 `MonthDetail`/`MonthlyStats`。 */
function stubMonthly(total: number, falsePositives: number, rate: number) {
  return {
    months: [{ month: '2026-10', count: total, fire: total, smoke: 0, false_positives: falsePositives, avg_conf: 0.9 }],
    detail: {
      month: '2026-10',
      total,
      fire: total,
      smoke: 0,
      false_positives: falsePositives,
      pending: 0,
      false_positive_rate: rate,
      avg_conf: 0.9,
      hourly: Array.from({ length: 24 }, () => 0),
      mom_pct: null,
    },
  }
}

/**
 * 空月不把「无从谈起」画成「0.0%」。
 *
 * 后端在整月无告警时也返回 `false_positive_rate: 0.0`（`stats_service.month_detail`），
 * 与「有告警但零误报」的真 0% 是同一个数。界面必须靠 `total` 把两者分开：
 * 空月显示「—」且不带百分号，而不是摊一个 0.0% 给用户。
 */
test('整月无告警时误报率显示「—」而不是 0.0%', async ({ page }) => {
  await page.route('**/api/stats/monthly**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(stubMonthly(0, 0, 0)),
    })
  })

  await page.goto('/bigscreen')

  const kpi = page.locator('.kpi').filter({ hasText: '误报率' })
  await expect(kpi.locator('.kpi__value')).toContainText('—')
  // 「—%」是错的样子：百分号必须跟着数字一起藏
  await expect(kpi.locator('.kpi__unit')).toHaveCount(0)
  await expect(kpi.locator('.kpi__sub')).toContainText('这个月没有告警')
})

/** 有告警但零误报，是真的 0.0%——要显示出来，不能被当成「没有数据」。 */
test('有告警但零误报时误报率是真的 0.0%', async ({ page }) => {
  await page.route('**/api/stats/monthly**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(stubMonthly(5, 0, 0)),
    })
  })

  await page.goto('/bigscreen')

  const kpi = page.locator('.kpi').filter({ hasText: '误报率' })
  await expect(kpi.locator('.kpi__value')).toContainText('0.0')
  await expect(kpi.locator('.kpi__unit')).toHaveText('%')
  await expect(kpi.locator('.kpi__sub')).toContainText('0 条已标误报')
})
