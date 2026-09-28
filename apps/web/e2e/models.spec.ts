import { expect, test } from '@playwright/test'

/**
 * 模型库页：归档列表、设为当前、下载、对比评估的界面接线。
 *
 * 前置：后端已起，`models/trained/` 里至少有两份归档。
 *
 * 两条踩过的坑，都写在这里：
 *
 * 1. **跳过与否问后端**（`/api/models`），不从页面上数行。第一版从页面数行，
 *    结果 `data-loaded` 时机不对、页面还停在空态，6 个用例全部 skip——报告上是
 *    「6 skipped」，看着像正常的按需跳过，实际是渲染坏了。
 * 2. **选择器必须限定到具体那张表**。页面上有归档表与对比结果表两张 `.fs-ledger`，
 *    用 `table.fs-ledger tbody tr` 会同时数到两边（实测 3 行变 5 行）。
 *    所以两张表各带 `data-table` 标识。
 *
 * 对比评估**不真跑**：真评估要跑完整 val 集（上千张图，实测约 30 秒/模型），
 * 端到端里用路由拦截造出任务响应。真评估的口径由后端测试覆盖。
 */

const ARCHIVES = 'table[data-table="archives"]'

async function archivedCount(page: import('@playwright/test').Page): Promise<number> {
  const res = await page.request.get('/api/models')
  expect(res.ok(), `/api/models 不可用（${res.status()}）——后端没起，或版本过旧`).toBe(true)
  return ((await res.json()) as unknown[]).length
}

async function openModels(page: import('@playwright/test').Page): Promise<void> {
  await page.goto('/models')
  // 等「首轮取数完成」，不是等「某个元素出现」：挂载后页面正好是空态
  await expect(page.locator('.models')).toHaveAttribute('data-loaded', 'true')
}

function archiveRows(page: import('@playwright/test').Page) {
  return page.locator(`${ARCHIVES} tbody tr`)
}

function archiveBoxes(page: import('@playwright/test').Page) {
  return page.locator(`${ARCHIVES} tbody tr input[type="checkbox"]`)
}

test('页面行数与后端口径一致', async ({ page }) => {
  const count = await archivedCount(page)
  await openModels(page)
  test.skip(count === 0, '没有归档，跳过')

  // 这条直接盯住「页面显示的和后端有的不一样」这类问题
  await expect(archiveRows(page)).toHaveCount(count)
  await expect(page.locator('.fs-empty')).toHaveCount(0)
})

test('无归档时给出方向而不是一句「暂无数据」', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count > 0, '有归档，空态不适用')

  await openModels(page)
  await expect(page.locator('.fs-empty')).toContainText('训练中心')
})

test('页面结构：当前权重、归档表、对比区', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count === 0, '没有归档，跳过')
  await openModels(page)

  await expect(page.locator('h2.fs-block__title').first()).toContainText('当前权重')

  const headers = await page.locator(`${ARCHIVES} thead th`).allInnerTexts()
  for (const h of ['归档', '轮数', 'mAP@50', '体积', '操作']) {
    expect(headers, `缺少列 ${h}`).toContain(h)
  }

  // 「为什么是它」：pinned 时无需解释（就是用户自己选的），其余情况必须有说明，
  // 否则「没指定过」与「指定的被删了」在界面上分不出来。这条断言依赖状态——
  // 之前一直是「没指定过」，所以没暴露。
  await expect(page.locator('.now__id')).not.toBeEmpty()
  const state = await (await page.request.get('/api/models/active')).json()
  if (state.source === 'pinned') {
    await expect(page.locator('.now')).toContainText('已指定')
  } else {
    await expect(page.locator('.now__note')).not.toBeEmpty()
  }

  // 恰好一行被标为当前
  await expect(page.locator(`${ARCHIVES} tbody tr.is-active`)).toHaveCount(1)
})

test('指标缺失显示「—」而不是 0', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count === 0, '没有归档，跳过')
  await openModels(page)

  // 只看归档表的数字列。对比结果表里「烟雾 / 明火」是「0.459 / 0.360」，
  // 不限定表就会把它当成非法值
  const cells = await page.locator(`${ARCHIVES} tbody tr td.is-num`).allInnerTexts()
  expect(cells.length).toBeGreaterThan(0)
  for (const c of cells) {
    const t = c.trim()
    expect(t === '—' || /^[\d.]+( MB)?$/.test(t), `意外的数值格内容: ${JSON.stringify(c)}`).toBe(
      true,
    )
  }
})

test('设为当前：改的是真正生效的那份，且能清除', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count < 2, '至少需要两份归档，跳过')

  // 先记下进入用例前的状态，收尾要**还原**它，而不是一律清空——
  // 「不留残渣」不等于「清空」：用户本来可能就有指定，清掉等于改了别人的设置。
  // 真发生过：一次全量 E2E 把用户指定的「当前权重」清成了「取最新」。
  const before = await (await page.request.get('/api/models/active')).json()

  await openModels(page)

  // 当前行的「设为当前」必须是禁用的——已经是当前了
  const activeRow = page.locator(`${ARCHIVES} tbody tr.is-active`)
  await expect(activeRow.getByRole('button', { name: '设为当前' })).toBeDisabled()

  // 挑一份不是当前的
  const other = page.locator(`${ARCHIVES} tbody tr:not(.is-active)`).first()
  const runId = await other.getAttribute('data-run-id')
  expect(runId).toBeTruthy()

  await other.getByRole('button', { name: '设为当前' }).click()

  // 它会变成当前那一行，且「当前权重」块跟着变
  await expect(page.locator(`${ARCHIVES} tbody tr.is-active`)).toHaveAttribute(
    'data-run-id',
    runId!,
  )
  await expect(page.locator('.now')).toContainText('已指定')

  // 而且真的改了线上用的那份——不是只改了列表上的标记
  const active = await (await page.request.get('/api/models/active')).json()
  expect(active.run_id).toBe(runId)

  // 清除指定要能回到「取最新」
  await page.getByRole('button', { name: '清除指定' }).click()
  await expect(page.locator('.now')).toContainText('取最新')
  expect((await (await page.request.get('/api/models/active')).json()).pinned).toBeNull()

  // 收尾：还原进入前的指定
  if (before.pinned) {
    await page.request.post('/api/models/active', { data: { run_id: before.pinned } })
  }
  const restored = await (await page.request.get('/api/models/active')).json()
  expect(restored.pinned, '收尾没有还原进入前的状态').toBe(before.pinned)
})

test('下载链接指向 download 接口且带 run_id', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count === 0, '没有归档，跳过')
  await openModels(page)

  const link = archiveRows(page).first().getByRole('link', { name: '下载' })
  const href = await link.getAttribute('href')
  expect(href).toContain('/api/models/download')
  expect(href).toContain('run_id=')
})

test('对比按钮要选够两份才可用', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count < 2, '至少需要两份归档，跳过')
  await openModels(page)

  const start = page.getByRole('button', { name: '开始对比' })
  await expect(start).toBeDisabled()

  const boxes = archiveBoxes(page)
  await boxes.nth(0).check()
  await expect(start).toBeDisabled() // 一份还不够

  await boxes.nth(1).check()
  await expect(start).toBeEnabled()
  // 勾选的两份要显示成 chip，让人确认选对了
  await expect(page.locator('.bar__picked .chip')).toHaveCount(2)
})

test('对比评估：进度按模型推进，结果标出最佳值', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count < 2, '至少需要两份归档，跳过')
  await openModels(page)

  const runs = (await archiveRows(page).evaluateAll((els) =>
    els.map((e) => e.getAttribute('data-run-id')),
  )).filter((v): v is string => Boolean(v))

  let polls = 0
  await page.route('**/api/eval', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    await route.fulfill({
      status: 201,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'fake-job',
        run_ids: runs.slice(0, 2),
        dataset: 'D-Fire/train',
        imgsz: 640,
        split: 'val',
        status: 'running',
        started_at: '2026-09-27T18:00:00',
        finished_at: null,
        done: 0,
        total: 2,
        results: [],
        error: null,
      }),
    })
  })
  await page.route('**/api/eval/jobs/fake-job', async (route) => {
    polls += 1
    const running = polls < 2
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: 'fake-job',
        run_ids: runs.slice(0, 2),
        dataset: 'D-Fire/train',
        imgsz: 640,
        split: 'val',
        status: running ? 'running' : 'done',
        started_at: '2026-09-27T18:00:00',
        finished_at: running ? null : '2026-09-27T18:02:00',
        done: running ? 1 : 2,
        total: 2,
        results: [
          {
            run_id: runs[0],
            mAP50: 0.71,
            mAP50_95: 0.39,
            precision: 0.73,
            recall: 0.64,
            per_class: { smoke: 0.44, fire: 0.34 },
            speed_ms: { inference: 12.5 },
            error: null,
          },
          {
            run_id: runs[1],
            mAP50: 0.65,
            mAP50_95: 0.36,
            precision: 0.7,
            recall: 0.6,
            per_class: { smoke: 0.4, fire: 0.3 },
            speed_ms: { inference: 11.0 },
            error: null,
          },
        ],
        error: null,
      }),
    })
  })

  const boxes = archiveBoxes(page)
  await boxes.nth(0).check()
  await boxes.nth(1).check()
  await page.getByRole('button', { name: '开始对比' }).click()

  // 先看到进度（done/total 来自任务，不是前端猜的）
  await expect(page.locator('.job__line')).toContainText('正在评估')
  await expect(page.locator('.job__line')).toContainText('2')

  // 完成后出结果表，最佳值要标出来
  await expect(page.locator('.job__line')).toContainText('评估完成')
  const resultRows = page.locator('table[data-table="eval"] tbody tr[data-eval-id]')
  await expect(resultRows).toHaveCount(2)
  await expect(resultRows.first().locator('.tag--best')).toHaveCount(1)
  // 结果表要带上「在什么条件下评的」，否则旧结果看起来像刚跑的
  await expect(page.locator('.job__meta')).toContainText('val')
  await expect(page.locator('.job__meta')).toContainText('imgsz=640')
  await expect(resultRows.first().locator('.note-cell')).toHaveCount(1)
})

test('删除入口：默认只删归档，勾选才连现场一起', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count === 0, '没有归档，跳过')
  await openModels(page)

  // 绝不点「确认删除」：这一步会真的动磁盘。只测弹窗与开关的行为，
  // 真正的删除语义由后端测试覆盖（含回收目录、缓存失效、当前权重回落）。
  const row = archiveRows(page).first()
  await row.getByRole('button', { name: '删除' }).click()

  const dialog = page.getByRole('dialog', { name: '确认删除' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('回收目录')

  // 默认不勾「连同训练现场」：两份数据不该被绑死
  const box = dialog.locator('input[type="checkbox"]')
  await expect(box).not.toBeChecked()
  await box.check()
  await expect(box).toBeChecked()

  // 取消之后不应留下任何痕迹
  await dialog.getByRole('button', { name: '取消' }).click()
  await expect(dialog).toHaveCount(0)
})

test('删除时把「当前权重」这件事说出来', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count === 0, '没有归档，跳过')
  await openModels(page)

  // 找到当前权重那一行点删除，弹窗必须提醒会回落——否则用户删完才发现
  // 识别页换了模型
  const activeRow = page.locator(`${ARCHIVES} tbody tr.is-active`).first()
  await activeRow.getByRole('button', { name: '删除' }).click()

  const dialog = page.getByRole('dialog', { name: '确认删除' })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('当前权重')
  await expect(dialog.locator('.confirm__warn')).toBeVisible()
  await dialog.getByRole('button', { name: '取消' }).click()
})

test('对比失败时把后端的说明显示出来', async ({ page }) => {
  const count = await archivedCount(page)
  test.skip(count < 2, '至少需要两份归档，跳过')
  await openModels(page)

  await page.route('**/api/eval', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({ detail: 'GPU 正被「train」占用' }),
    })
  })

  const boxes = archiveBoxes(page)
  await boxes.nth(0).check()
  await boxes.nth(1).check()
  await page.getByRole('button', { name: '开始对比' }).click()

  const err = page.locator('.fs-note-error')
  await expect(err).toBeVisible()
  await expect(err).toContainText('GPU 正被')
})
