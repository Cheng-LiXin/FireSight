import { rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test } from '@playwright/test'

/**
 * 训练中心的端到端验证。
 *
 * 真训练那条会**真的起一次训练并写出归档**，所以默认跳过，靠环境变量显式开启：
 *
 *     $env:FIRESIGHT_E2E_TRAIN="1"; npx playwright test training
 *
 * 它自己负责清理产物（删掉本次的 runs/ 现场与 models/ 归档）。默认的
 * `npm run test:e2e` 因此保持快速且无副作用。
 *
 * **门控必须写在用例内部，不能用文件级 `beforeEach`。** 之前就是文件级门控，
 * 于是同文件里那条不需要训练的 409 用例也一起被跳过——它在默认跑里从没执行过，
 * 报告上只显示「2 skipped」，看起来像正常的按需跳过。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(HERE, '../../..')

const IMG_SIZE = 320
const EPOCHS = 2

test('启动训练：实时曲线逐轮长出，结束后出现在训练记录里', async ({ page }) => {
  test.skip(!process.env.FIRESIGHT_E2E_TRAIN, '需设 FIRESIGHT_E2E_TRAIN=1（会真的跑训练）')

  // 这条要等真训练跑完，必须高于配置里的默认 60s——否则断言里写再长的 timeout
  // 也没用，整个用例会先被掐掉。
  test.setTimeout(300_000)

  await page.goto('/training')

  const launcher = page.locator('.launcher')
  await expect(launcher).toBeVisible()

  // 必须显式选数据集：下拉默认取列表第一个，那是 D-Fire/raw（14122 张），
  // 在 CPU 上跑 0.2 的比例也要几十分钟。demo_fire 只有十几张，几十秒能完。
  await launcher.getByLabel('数据集').selectOption('demo_fire')

  // 用最小配置，几十秒能跑完
  await launcher.getByLabel('轮数').fill(String(EPOCHS))
  await launcher.getByLabel('imgsz').fill(String(IMG_SIZE))
  await launcher.getByLabel('子集比例').fill('0.2')
  await launcher.locator('select').last().selectOption('cpu')

  const before = await page.locator('table.fs-ledger tbody tr').count()

  await launcher.getByRole('button', { name: '开始训练' }).click()

  // 状态条出现且进入 running
  const status = page.locator('.status')
  await expect(status).toBeVisible({ timeout: 30_000 })
  await expect(status).toContainText('训练中')

  // 实时曲线逐轮长出来：等到 epoch 计数达到配置值
  await expect(status).toContainText(`${EPOCHS} / ${EPOCHS}`, { timeout: 240_000 })

  // 训练结束：状态转为已完成并给出 run_id
  await expect(status).toContainText('已完成', { timeout: 120_000 })
  const runId = (await status.innerText()).match(/归档为\s*(\S+)/)?.[1]
  expect(runId, '完成后应给出归档的 run_id').toBeTruthy()

  try {
    // 训练记录应多出一行
    await expect
      .poll(async () => page.locator('table.fs-ledger tbody tr').count(), { timeout: 30_000 })
      .toBeGreaterThan(before)
  } finally {
    // 本次是真的写了产物，收尾清掉，免得污染模型库
    if (runId) {
      for (const p of [
        path.join(ROOT, 'models/trained', runId),
        path.join(ROOT, 'runs/training', runId),
      ]) {
        rmSync(p, { recursive: true, force: true })
      }
    }
  }
})

test('GPU 被占用时，界面把后端的 409 说明显示出来', async ({ page }) => {
  // 端到端里没法真占住 GPU 锁（那需要另一条真训练），所以用路由拦截造出后端那个
  // 409，本用例验证的是**界面把 detail 透传并渲染出来**这条路径；判定逻辑本身由
  // 后端 test_17 / test_20 覆盖。
  await page.route('**/api/train/jobs', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      // 与 deps.GpuBusy 的实际文案一致
      body: JSON.stringify({ detail: 'GPU 正被「train」占用' }),
    })
  })

  await page.goto('/training')

  const launcher = page.locator('.launcher')
  await expect(launcher).toBeVisible()

  const start = launcher.getByRole('button', { name: '开始训练' })
  await expect(start).toBeEnabled()
  await start.click()

  // 后端的可读中文说明要原样出现在界面上，而不是被吞成「提交失败」之类
  const err = page.locator('.fs-note-error')
  await expect(err).toBeVisible()
  await expect(err).toContainText('GPU 正被')
  await expect(err).toContainText('train')

  // 没有训练被真的起起来，所以不该进入「训练进行中」
  await expect(launcher.getByRole('button', { name: '开始训练' })).toBeEnabled()
  await expect(page.locator('.status')).toHaveCount(0)
})
