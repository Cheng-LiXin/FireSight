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
  const t = await page
    .locator('.panel__title .num')
    .first()
    .innerText()
    .catch(() => '0')
  return Number(t.replace(/\D/g, '')) || 0
}

async function openEvents(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/events')
  await awaitData(page)
}

test('页面结构与导出入口', async ({ page }) => {
  await openEvents(page)

  await expect(page.locator('.tray')).toBeVisible()
  // 筛选控件齐全：关键词 + 类别 + 状态 + 起止日期 + 每页
  await expect(page.locator('.tray input[type="date"]')).toHaveCount(2)
  await expect(page.locator('.tray select')).toHaveCount(3)

  // 导出是链接交给浏览器原生下载，href 必须落在 events/export 上
  const href = await page.locator('.head__actions a').getAttribute('href')
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

  await page.locator('.tray select').first().selectOption('fire')
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

test('状态筛选：四个状态各自只显示该状态的行', async ({ page }) => {
  await openEvents(page)
  test.skip((await totalRows(page)) === 0, '事件库为空，跳过')

  const statusSelect = page.locator('.tray select').nth(1)

  // 每个状态筛下去，行里那个状态标签必须唯一且正确。
  // .filter({ hasText }) 是子串匹配，所以「误报」不会误吃到「已标误报」这种旧文案，
  // 「待处置」也不会吃到别的——四个文案互不为子串。
  for (const [value, label] of [
    ['pending', '待处置'],
    ['confirmed', '已确认'],
    ['false_positive', '误报'],
    ['resolved', '已处置'],
  ] as const) {
    await statusSelect.selectOption(value)
    await page.waitForTimeout(600)
    const rows = page.locator('table.fs-ledger tbody tr')
    const n = await rows.count()
    for (let i = 0; i < n; i++) {
      await expect(rows.nth(i).locator('td .tag').filter({ hasText: label })).toHaveCount(1)
    }
  }
})

test('状态筛选：误报行只给撤销、不再给标误报', async ({ page }) => {
  await openEvents(page)
  test.skip((await totalRows(page)) === 0, '事件库为空，跳过')

  await page.locator('.tray select').nth(1).selectOption('false_positive')
  await page.waitForTimeout(600)
  const fpRows = page.locator('table.fs-ledger tbody tr')
  const n = await fpRows.count()
  test.skip(n === 0, '还没有标过误报的事件，跳过')

  for (let i = 0; i < n; i++) {
    const row = fpRows.nth(i)
    // 已定性的行不该再给「标为误报」——按钮按状态条件渲染，不是摆出来再禁用
    await expect(row.getByRole('button', { name: '标为误报' })).toHaveCount(0)
    await expect(row.getByRole('button', { name: '撤销误报' })).toBeVisible()
  }
})

test('待处置行给确认与标误报，不给撤销', async ({ page }) => {
  await openEvents(page)
  test.skip((await totalRows(page)) === 0, '事件库为空，跳过')

  await page.locator('.tray select').nth(1).selectOption('pending')
  await page.waitForTimeout(600)
  const rows = page.locator('table.fs-ledger tbody tr')
  const n = await rows.count()
  test.skip(n === 0, '没有待处置事件，跳过')

  for (let i = 0; i < n; i++) {
    const row = rows.nth(i)
    await expect(row.getByRole('button', { name: '确认' })).toBeVisible()
    await expect(row.getByRole('button', { name: '标为误报' })).toBeVisible()
    await expect(row.getByRole('button', { name: '撤销误报' })).toHaveCount(0)
    // 「标记已处置」只在已确认时出现——没确认就宣布处理完是不成立的
    await expect(row.getByRole('button', { name: '标记已处置' })).toHaveCount(0)
  }
})

test('分页翻页不重复', async ({ page }) => {
  await openEvents(page)
  test.skip((await totalRows(page)) <= 20, '记录不足一页，跳过')

  await page.locator('.tray select').nth(2).selectOption('20')
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

  await page.locator('.tray select').first().selectOption('smoke')
  await page.locator('.tray input').first().fill('北门')

  const href = await page.locator('.head__actions a').getAttribute('href')
  expect(href).toContain('label=smoke')
  expect(href).toContain('keyword=')
  // 空条件不该出现在 URL 里
  expect(href).not.toContain('start=')
})

test('下载片段：地址带 download 参数', async ({ page }) => {
  await openEvents(page)
  test.skip((await totalRows(page)) === 0, '事件库为空，跳过')

  const dl = page.locator('[data-testid^="clip-dl-"]')
  test.skip((await dl.count()) === 0, '没有任何事件留存片段，跳过')

  const href = await dl.first().getAttribute('href')
  expect(href).toContain('/clip')
  expect(href).toContain('download=true')

  // 后端要真的当附件回——否则浏览器会在当前页直接播放
  const res = await page.request.get(href as string)
  expect(res.status()).toBe(200)
  expect(res.headers()['content-disposition']).toContain('attachment')
})

test('状态流转：已确认行的动作换掉（打桩，不动真实事件库）', async ({ page }) => {
  await openEvents(page)
  test.skip((await totalRows(page)) === 0, '事件库为空，跳过')

  // 状态变更会写库。E2E 去改真实事件库，每跑一次就少一条待处置，测试不可重复——
  // 后端测试已经用临时库覆盖了写入语义，这里只验证界面：点了确认之后，
  // 这一行的动作是不是换成了「标记已处置」。
  const EVENT_ID = 'evt-stub-1'
  await page.route('**/api/events**', async (route) => {
    const url = route.request().url()
    const method = route.request().method()
    if (method === 'POST' && url.includes('/confirm')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          event_id: EVENT_ID,
          review_status: 'confirmed',
          is_false_positive: false,
        }),
      })
      return
    }
    if (method === 'GET' && url.includes('/api/events?')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [
            {
              event_id: EVENT_ID,
              ts: '2026-09-27T15:03:07',
              frame_index: 12,
              labels: ['fire'],
              conf: 0.912,
              location: '仓库',
              message: '检测到明火，位置：仓库',
              has_clip: false,
              is_false_positive: false,
              review_status: 'confirmed',
            },
          ],
          total: 1,
          limit: 20,
          offset: 0,
        }),
      })
      return
    }
    await route.fallback()
  })

  await page.reload()
  await awaitData(page)

  const row = page.locator(`[data-event-id="${EVENT_ID}"]`)
  // 一行里有两类 .tag（类别 / 状态），不加区分会命中两个元素触发严格模式报错
  await expect(row.locator('td .tag').filter({ hasText: '已确认' })).toHaveText('已确认')
  // 已确认行：给「标记已处置」，不再给「确认」
  await expect(row.getByRole('button', { name: '标记已处置' })).toBeVisible()
  await expect(row.getByRole('button', { name: '确认' })).toHaveCount(0)
  await expect(row.getByRole('button', { name: '撤销误报' })).toHaveCount(0)

  await row.getByRole('button', { name: '标记已处置' }).click()
  await page.waitForTimeout(500)
})

test('写错的筛选值不会安静地返回空——后端 400 会显示出来', async ({ page }) => {
  // 界面上的下拉只提供合法值，这里直接打接口验证校验真的生效
  const res = await page.request.get('/api/events?label=flame')
  expect(res.status()).toBe(400)
  expect(await res.text()).toContain('flame')
})

/**
 * 连续输入的关键词不逐字发请求。
 *
 * 关键词是连续输入，逐字取数既是无谓的负载，也让乱序响应有了可乘之机。
 * 这条断言的是「防抖真的在」，而不是某一次请求快慢——所以不依赖真实数据。
 */
test('关键词连续输入只发一次取数请求（防抖）', async ({ page }) => {
  const hits: string[] = []
  await page.route('**/api/events?**', async (route) => {
    hits.push(route.request().url())
    await route.fallback()
  })

  await page.goto('/events')
  await awaitData(page)
  await page.waitForTimeout(300)
  const before = hits.length

  // 每 60ms 敲一个字，间隔远小于 300ms 的防抖窗口
  const box = page.locator('.tray input').first()
  await box.click()
  for (const ch of ['北', '门', '大', '街']) {
    await page.keyboard.type(ch)
    await page.waitForTimeout(60)
  }
  await page.waitForTimeout(1200)

  expect(
    hits.length - before,
    `4 次连续输入应因防抖收敛为 1 次取数，实际 ${hits.length - before} 次`,
  ).toBe(1)
})

/**
 * 慢的旧响应不能覆盖快的新结果。
 *
 * 用打桩数据把两次响应做成可区分的（旧=全量 3 条、新=筛选后 2 条），
 * 并让先发出的那次固定慢 1500ms。没有序号守卫时，旧响应最后落地，
 * 屏上就会留回全量列表——那正是 `TrainingView.limitsSeq` 记着的坑。
 *
 * 注意：不能靠「真实数据的行数」来判断。真实接口的 limit=20，全量与筛选后
 * 都可能是 20 行，行数根本区分不出来（本探针第一版就是这么假通过的）。
 */
test('慢的旧响应不会覆盖快的新结果（乱序守卫）', async ({ page }) => {
  const mk = (id: string, msg: string, rs: 'pending' | 'confirmed') => ({
    event_id: id,
    ts: '2026-10-01T10:00:00',
    frame_index: 1,
    labels: ['fire'],
    conf: 0.9,
    location: '北门',
    message: msg,
    has_clip: false,
    is_false_positive: false,
    review_status: rs,
  })

  await page.route('**/api/events?**', async (route) => {
    const status = new URL(route.request().url()).searchParams.get('status')
    if (status !== 'pending') {
      await new Promise((r) => setTimeout(r, 1500))
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          items: [mk('A1', '旧-全量-1', 'confirmed'), mk('A2', '旧-全量-2', 'confirmed'), mk('A3', '旧-全量-3', 'confirmed')],
          total: 3,
          limit: 20,
          offset: 0,
        }),
      })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [mk('P1', '新-待处置-1', 'pending'), mk('P2', '新-待处置-2', 'pending')],
        total: 2,
        limit: 20,
        offset: 0,
      }),
    })
  })

  await page.goto('/events')
  await page.waitForTimeout(600)
  // 初始请求还在路上，此刻切一个离散筛选（立即取数，不等防抖）
  await page.locator('.tray select').nth(1).selectOption('pending')
  await page.waitForTimeout(2500)

  const msgs = await page.locator('table.fs-ledger tbody tr td.msg').allInnerTexts()
  expect(msgs, '慢的旧响应把列表覆盖回去了').toEqual(['新-待处置-1', '新-待处置-2'])
})
