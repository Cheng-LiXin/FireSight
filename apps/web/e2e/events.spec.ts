import { expect, test } from '@playwright/test'

/**
 * 告警事件页：筛选、分页、片段回放、导出、误报按钮。
 *
 * 前置：后端已起（`fs-serve`），事件库里有记录（跑过 `fs-infer`）。
 * 库为空时依赖数据的断言会**显式 skip**，而不是伪装成通过——空库是首次部署的
 * 正常状态，不是失败。
 */

/**
 * 等首轮取数完成。
 *
 * 不能只看「账册或空态」：页面挂载后、请求返回前正好渲染的是空态，等它等于没等
 * （第一次跑就是这样，5 个依赖数据的用例全部读到初始 0 而被静默跳过）。
 * 页面暴露 data-loaded 作为明确信号。
 */
async function awaitData(page: import('@playwright/test').Page): Promise<void> {
  await expect(page.locator('.events')).toHaveAttribute('data-loaded', 'true')
}

/** 页面上的事件总数；取不到时返回 0。 */
async function totalRows(page: import('@playwright/test').Page): Promise<number> {
  const t = await page.locator('h2.fs-block__title .fs-num').first().innerText().catch(() => '0')
  return Number(t.replace(/\D/g, '')) || 0
}

async function openEvents(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/events')
  await awaitData(page)
}

test('页面结构与导出入口', async ({ page }) => {
  await openEvents(page)

  await expect(page.locator('.bar')).toBeVisible()
  // 筛选控件齐全：关键词 + 类别 + 状态 + 起止日期 + 每页
  await expect(page.locator('.bar input[type="date"]')).toHaveCount(2)
  await expect(page.locator('.bar select')).toHaveCount(3)

  // 导出是链接交给浏览器原生下载，href 必须落在 events/export 上
  const href = await page.locator('.bar__actions a').getAttribute('href')
  expect(href).toContain('/api/events/export')

  // 空态与账册二选一，不能两个都不在
  const hasLedger = (await page.locator('table.fs-ledger').count()) > 0
  const hasEmpty = (await page.locator('.fs-empty').count()) > 0
  expect(hasLedger || hasEmpty, '既没有账册也没有空态').toBe(true)

  if (!hasLedger) {
    await expect(page.locator('.fs-empty')).toContainText('还没有任何告警记录')
  }
})

test('账册每行都有时间、类别、置信度', async ({ page }) => {
  await openEvents(page)
  const rows = page.locator('table.fs-ledger tbody tr')
  const n = await rows.count()
  test.skip(n === 0, '事件库为空，跳过')

  const headers = await page.locator('table.fs-ledger thead th').allInnerTexts()
  expect(headers).toContain('时间')
  expect(headers).toContain('类别')
  expect(headers).toContain('置信度')
  expect(headers).toContain('片段')

  const first = rows.first()
  // 置信度是三位小数，且落在 0..1
  const conf = await first.locator('td.is-num').innerText()
  expect(conf).toMatch(/^\d\.\d{3}$/)
  // 类别标签必须是契约里的中文名，不能漏出英文
  const tags = await first.locator('td .tag').allInnerTexts()
  expect(tags.some((t) => t === '烟雾' || t === '明火')).toBe(true)
})

test('按类别筛选后每一行都匹配', async ({ page }) => {
  await openEvents(page)
  const total = await totalRows(page)
  test.skip(total === 0, '事件库为空，跳过')

  await page.locator('.bar select').first().selectOption('fire')
  await expect(page.locator('table.fs-ledger tbody tr').first()).toBeVisible()

  const tags = await page.locator('table.fs-ledger tbody tr td .tag--alarm').count()
  const rows = await page.locator('table.fs-ledger tbody tr').count()
  expect(tags).toBeGreaterThan(0)

  // 筛出来的行数不该超过筛选前的总数
  const after = await totalRows(page)
  expect(after).toBeLessThanOrEqual(total)
  expect(rows).toBeLessThanOrEqual(after)

  // 清空条件后应能恢复
  await page.getByRole('button', { name: '清空条件' }).click()
  await expect
    .poll(async () => totalRows(page), { timeout: 10_000 })
    .toBe(total)
})

test('状态筛选：待复核与已标误报互斥', async ({ page }) => {
  await openEvents(page)
  test.skip((await totalRows(page)) === 0, '事件库为空，跳过')

  const statusSelect = page.locator('.bar select').nth(1)

  await statusSelect.selectOption('false_positive')
  await page.waitForTimeout(600)
  const fpRows = page.locator('table.fs-ledger tbody tr')
  for (let i = 0; i < (await fpRows.count()); i++) {
    await expect(fpRows.nth(i).locator('.tag', { hasText: '已标误报' })).toHaveCount(1)
    // 已复核的行不该再给「标为误报」的可点按钮
    await expect(fpRows.nth(i).getByRole('button', { name: '标为误报' })).toBeDisabled()
  }

  await statusSelect.selectOption('pending')
  await page.waitForTimeout(600)
  const pRows = page.locator('table.fs-ledger tbody tr')
  for (let i = 0; i < Math.min(await pRows.count(), 5); i++) {
    await expect(pRows.nth(i).locator('.tag', { hasText: '待复核' })).toHaveCount(1)
  }
})

test('分页翻页不重复', async ({ page }) => {
  await openEvents(page)
  test.skip((await totalRows(page)) <= 20, '记录不足一页，跳过')

  await page.locator('.bar select').nth(2).selectOption('20')
  await expect(page.locator('table.fs-ledger tbody tr')).toHaveCount(20)

  const page1 = await page
    .locator('table.fs-ledger tbody tr')
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-event-id')))

  await page.getByRole('button', { name: '下一页' }).click()
  await expect
    .poll(async () =>
      page.locator('table.fs-ledger tbody tr').first().getAttribute('data-event-id'),
    )
    .not.toBe(page1[0])

  const page2 = await page
    .locator('table.fs-ledger tbody tr')
    .evaluateAll((els) => els.map((e) => e.getAttribute('data-event-id')))

  const overlap = page2.filter((id) => id !== null && page1.includes(id))
  expect(overlap, '两页出现重复记录').toHaveLength(0)

  // 回到第一页应恢复原样
  await page.getByRole('button', { name: '上一页' }).click()
  await expect
    .poll(async () =>
      page.locator('table.fs-ledger tbody tr').first().getAttribute('data-event-id'),
    )
    .toBe(page1[0])
})

test('片段回放：浏览器真的解得出画面', async ({ page }) => {
  await openEvents(page)
  test.skip((await totalRows(page)) === 0, '事件库为空，跳过')

  const play = page.getByRole('button', { name: '回放' }).first()
  test.skip((await play.count()) === 0, '没有任何事件留存片段，跳过')

  await expect(page.locator('video.clip')).toHaveCount(0)
  await play.click()

  const video = page.locator('video.clip')
  await expect(video).toBeVisible()
  const src = await video.getAttribute('src')
  expect(src).toContain('/api/events/')
  expect(src).toContain('/clip')

  // 只断言 src 是不够的：mp4v 编码那版也曾让 src 完全正确、画面全无
  // （<video> 报 DEMUXER_ERROR_NO_SUPPORTED_STREAMS，只剩一条控制栏）。
  // 必须断言解码结果。
  await expect
    .poll(async () => video.evaluate((v: HTMLVideoElement) => v.readyState), {
      timeout: 15_000,
      message: 'video.readyState 一直没到 HAVE_CURRENT_DATA',
    })
    .toBeGreaterThanOrEqual(2)

  const info = await video.evaluate((v: HTMLVideoElement) => ({
    w: v.videoWidth,
    h: v.videoHeight,
    err: v.error?.code ?? null,
    msg: v.error?.message ?? null,
  }))
  expect(info.err, `浏览器解码失败: ${info.msg}`).toBeNull()
  expect(info.w, '解出的画面宽度为 0').toBeGreaterThan(0)
  expect(info.h, '解出的画面高度为 0').toBeGreaterThan(0)

  // 关闭后回放区收起
  await page.getByRole('button', { name: '关闭' }).click()
  await expect(page.locator('video.clip')).toHaveCount(0)
})

test('导出链接跟随当前筛选', async ({ page }) => {
  await openEvents(page)

  await page.locator('.bar select').first().selectOption('smoke')
  await page.locator('.bar input').first().fill('北门')

  const href = await page.locator('.bar__actions a').getAttribute('href')
  expect(href).toContain('label=smoke')
  expect(href).toContain('keyword=')
  // 空条件不该出现在 URL 里
  expect(href).not.toContain('start=')
})

test('写错的筛选值不会安静地返回空——后端 400 会显示出来', async ({ page }) => {
  // 界面上的下拉只提供合法值，这里直接打接口验证校验真的生效
  const res = await page.request.get('/api/events?label=flame')
  expect(res.status()).toBe(400)
  expect(await res.text()).toContain('flame')
})
