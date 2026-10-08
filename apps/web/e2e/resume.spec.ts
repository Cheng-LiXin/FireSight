import { expect, test } from '@playwright/test'

/**
 * 断点续训的界面接线。
 *
 * 两条判据都直接问后端（训练记录列表），不从页面上猜：
 * `resumable` 为真才有关闭按钮，为假必须给出**原因**而不是一个灰按钮。
 *
 * **不真续训**：拦截 `POST /api/train/jobs`，只断言界面送出的载荷对不对。
 * 真续训要几十分钟。真机制（轮次从 41 接上、results.csv 追加、优化器步数延续）
 * 由后端测试与一次人工验证覆盖。
 */

const START = '**/api/train/jobs'

async function runs(page: import('@playwright/test').Page) {
  const res = await page.request.get('/api/train/runs')
  expect(res.ok(), `/api/train/runs 不可用（${res.status()}）`).toBe(true)
  return (await res.json()) as Array<{
    run_id: string
    batch: number | null
    imgsz: number | null
    resumable: boolean
    resume_note: string
    resume_done: number | null
    resume_total: number | null
  }>
}

test('可续训的现场有「继续训练」，不可续的给出原因', async ({ page }) => {
  const list = await runs(page)
  test.skip(list.length === 0, '还没有任何训练记录')

  await page.goto('/training')
  const ledger = page.locator('table.fs-ledger').first()
  await expect(ledger).toBeVisible()

  const resumable = list.filter((r) => r.resumable)
  // 每一行都要有明确交代：按钮 或 原因，不能两者都无
  for (const r of list.slice(0, 6)) {
    const row = ledger.locator(`tbody tr[data-run-id="${r.run_id}"]`)
    if (await row.count()) {
      if (r.resumable) {
        await expect(row.getByRole('button', { name: '继续训练' })).toBeVisible()
      } else {
        const why = row.locator('.why')
        await expect(why).toHaveText('不可续训')
        // 「不可续训」三个字不够——悬停必须能看到理由
        expect(await why.getAttribute('title')).toBeTruthy()
      }
    }
  }
  test.skip(resumable.length === 0, '当前没有可续训的现场（都被跑完了）')
})

test('点「继续训练」送出的是最小载荷：只给现场，几何由后端取', async ({ page }) => {
  const list = await runs(page)
  const target = list.find((r) => r.resumable)
  test.skip(!target, '当前没有可续训的现场')

  let payload: Record<string, unknown> | null = null
  await page.route(START, async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    payload = JSON.parse(route.request().postData() || '{}')
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'fake-resume',
        dataset: target!.run_id,
        epochs: target!.resume_total,
        status: 'running',
        started_at: '2026-09-27T23:00:00',
        finished_at: null,
        stop_requested_at: null,
        run_id: null,
        error: null,
        returncode: null,
        config: {},
        event_count: 0,
      }),
    })
  })

  // 续训前会确认一次（几十轮的训练，误点代价大）。原先用 window.confirm，
  // 靠 page.on('dialog') 拦原生弹窗；换成 ElMessageBox 之后它渲染成页面里的
  // role=dialog 元素，原生 dialog 事件不再触发，断言必须跟着换。
  let nativeDialog = false
  page.on('dialog', (d) => {
    nativeDialog = true
    void d.accept()
  })

  await page.goto('/training')
  const row = page.locator(`table.fs-ledger tbody tr[data-run-id="${target!.run_id}"]`)
  await row.getByRole('button', { name: '继续训练' }).click()

  const confirm = page.getByRole('dialog', { name: '断点续训' })
  await expect(confirm).toBeVisible()
  // 确认框要把「从第几轮继续」说清楚
  await expect(confirm).toContainText(`第 ${(target!.resume_done ?? 0) + 1} 轮`)
  // 取消是正常路径，不该发出请求
  await confirm.getByRole('button', { name: '取消' }).click()
  await expect(confirm).toBeHidden()
  expect(payload).toBeNull()
  expect(nativeDialog).toBe(false)

  // 再走一遍，这次确认：载荷里**不该**出现几何——它们由后端从检查点取。
  // 出现了就说明前端在假装能改。
  await row.getByRole('button', { name: '继续训练' }).click()
  await page.getByRole('dialog', { name: '断点续训' }).getByRole('button', { name: '继续训练' }).click()

  await expect.poll(() => payload !== null, { timeout: 10_000 }).toBe(true)
  expect(payload!.resume_run_id).toBe(target!.run_id)
  expect(payload!.imgsz).toBeUndefined()
  expect(payload!.batch).toBeUndefined()
  expect(payload!.epochs).toBeUndefined()
})

test('续训被后端拒时，把理由显示出来', async ({ page }) => {
  const list = await runs(page)
  const target = list.find((r) => r.resumable)
  test.skip(!target, '当前没有可续训的现场')

  await page.route(START, async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    await route.fulfill({
      status: 400,
      contentType: 'application/json',
      body: JSON.stringify({
        detail: '无法从这个现场续训：训练已结束，检查点里的优化器状态已被剥离——只能拿该轮权重微调',
      }),
    })
  })
  page.on('dialog', (d) => void d.accept())

  await page.goto('/training')
  await page
    .locator(`table.fs-ledger tbody tr[data-run-id="${target!.run_id}"]`)
    .getByRole('button', { name: '继续训练' })
    .click()
  // ElMessageBox 不会触发原生 dialog 事件，得自己点掉确认
  await page.getByRole('dialog', { name: '断点续训' }).getByRole('button', { name: '继续训练' }).click()

  const err = page.locator('.fs-note-error')
  await expect(err).toBeVisible()
  await expect(err).toContainText('优化器')
})

test('训练记录里 batch / imgsz 两列显示的是后端的值', async ({ page }) => {
  // 后端读不到 args.yaml 的现场，这两格是「—」。所以挑一条**取得到几何**的来对，
  // 不能随便取第一行——老现场缺 args.yaml 时断言会假失败。
  const list = await runs(page)
  const target = list.find((r) => r.batch !== null || r.imgsz !== null)
  test.skip(!target, '所有现场都没有 args.yaml，量不到几何')

  await page.goto('/training')
  const ledger = page.locator('table.fs-ledger').first()
  await expect(ledger).toBeVisible()

  const headers = await ledger.locator('thead th').allInnerTexts()
  expect(headers).toContain('batch')
  expect(headers).toContain('imgsz')

  // 按表头定位列，别按下标写死——以后加列不会把这条测试变成假失败
  const batchCol = headers.indexOf('batch')
  const imgszCol = headers.indexOf('imgsz')

  const cell = async (col: number) =>
    (await ledger
      .locator(`tbody tr[data-run-id="${target!.run_id}"] td`)
      .nth(col)
      .innerText()).trim()

  expect(await cell(batchCol)).toBe(target!.batch === null ? '—' : String(target!.batch))
  expect(await cell(imgszCol)).toBe(target!.imgsz === null ? '—' : String(target!.imgsz))
})
