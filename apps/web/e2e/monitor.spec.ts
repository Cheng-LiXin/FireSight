import { expect, test } from '@playwright/test'

/** 实时监控页：结构、启动门槛、后端错误透出。 */

test('监控页结构：表单、状态条、画面区都在', async ({ page }) => {
  await page.goto('/monitor')

  await expect(page.getByRole('heading', { name: '实时监控' })).toBeVisible()
  await expect(page.getByLabel('视频源')).toBeVisible()
  await expect(page.getByLabel('位置名')).toBeVisible()
  await expect(page.getByText('状态')).toBeVisible()
  await expect(page.getByText('画面')).toBeVisible()
})

test('视频源为空时不能启动', async ({ page }) => {
  await page.goto('/monitor')

  await expect(page.getByRole('button', { name: '开始监控' })).toBeDisabled()
  await page.getByLabel('视频源').fill('rtsp://127.0.0.1:554/stream')
  await expect(page.getByRole('button', { name: '开始监控' })).toBeEnabled()
})

test('监控跑起来时能人工暂停/恢复语音播报', async ({ page }) => {
  await page.route('**/api/monitor/status', async (route) => {
    await route.fulfill({
      json: {
        status: 'running',
        source: 'rtsp://127.0.0.1:554/stream',
        camera_name: '东门',
        started_at: '12:00:00',
        finished_at: null,
        frames: 120,
        alerts: 2,
        reconnects: 0,
        error: null,
        tts_paused: false,
      },
    })
  })

  let requested: unknown = null
  await page.route('**/api/monitor/tts/pause', async (route) => {
    requested = route.request().postDataJSON()
    await route.fulfill({ json: { paused: true, tts_paused: true, status: 'running' } })
  })

  await page.goto('/monitor')

  await expect(page.getByTestId('mon-tts-state')).toHaveText('播报中')
  await page.getByTestId('mon-tts-pause').click()

  expect(requested).toEqual({ paused: true })
  await expect(page.getByTestId('mon-tts-state')).toHaveText('已暂停')
  await expect(page.getByTestId('mon-tts-resume')).toBeVisible()
})

test('本地源读不了时把失败说清楚，而不是一直转圈', async ({ page }) => {
  await page.goto('/monitor')

  await page.getByLabel('视频源').fill('Z:/definitely/missing.mp4')
  await page.getByRole('button', { name: '开始监控' }).click()

  // 后端会把「视频不存在」之类的原因透传回来；状态落到失败
  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible({ timeout: 15_000 })
  await expect(alert).toContainText(/不存在|无法|失败/)
  await expect(page.getByText('失败')).toBeVisible()
})
