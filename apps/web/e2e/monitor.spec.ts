import { expect, test, type Page } from '@playwright/test'

/** 实时监控页：结构、左右等高布局、启动门槛、后端错误透出。 */

/** 视频源现在是下拉框：设备由后端给，界面不拼地址。
 *  末尾那项「自定义…」点开才是文本框——本地视频、图片目录、临时开的流走它。 */
async function pickCustomSource(page: Page, value: string) {
  await page.getByLabel('视频源').selectOption({ label: '自定义…（地址或本地路径）' })
  await page.getByLabel('地址或路径').fill(value)
}

test('监控页结构：表单、状态条、画面区都在', async ({ page }) => {
  await page.goto('/monitor')

  await expect(page.getByRole('heading', { name: '实时监控' })).toBeVisible()
  await expect(page.getByLabel('视频源')).toBeVisible()
  await expect(page.getByLabel('位置名')).toBeVisible()
  await expect(page.getByText('状态')).toBeVisible()
  await expect(page.getByText('画面')).toBeVisible()
})

test('参数框与画面框左右并排且等高', async ({ page }) => {
  await page.goto('/monitor')

  const tray = page.getByRole('region', { name: '启动参数' })
  const stage = page.locator('.panel').filter({ hasText: '画面' })
  await expect(tray).toBeVisible()

  const t = await tray.boundingBox()
  const s = await stage.boundingBox()
  expect(t).not.toBeNull()
  expect(s).not.toBeNull()
  // 左右：参数框在画面左边，不是上下堆叠
  expect(t!.x + t!.width).toBeLessThanOrEqual(s!.x + 2)
  // 等高：两框下沿对齐
  expect(Math.abs(t!.y + t!.height - (s!.y + s!.height))).toBeLessThanOrEqual(2)
})

test('视频源下拉列出后端给的设备，选中后位置名跟着填', async ({ page }) => {
  await page.route('**/api/monitor/sources', async (route) => {
    await route.fulfill({
      json: [
        { key: 'rtsp://10.0.0.7/live', label: '东门', kind: 'stream', detail: 'rtsp://10.0.0.7/live' },
        { key: '0', label: '本机摄像头 0', kind: 'camera', detail: '640×480 · 30 fps' },
      ],
    })
  })

  await page.goto('/monitor')

  const source = page.getByLabel('视频源')
  await expect(source.locator('option', { hasText: '东门' })).toHaveCount(1)
  await expect(source.locator('option', { hasText: '本机摄像头 0' })).toHaveCount(1)

  await source.selectOption('rtsp://10.0.0.7/live')
  // 设备名就是现场的位置名，事件与播报里要用的就是它
  await expect(page.getByLabel('位置名')).toHaveValue('东门')
  // 选本机摄像头不该抢位置名
  await source.selectOption('0')
  await expect(page.getByLabel('位置名')).toHaveValue('东门')
})

test('视频源为空时不能启动', async ({ page }) => {
  await page.goto('/monitor')

  await expect(page.getByRole('button', { name: '开始监控' })).toBeDisabled()
  await pickCustomSource(page, 'rtsp://127.0.0.1:554/stream')
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

  await pickCustomSource(page, 'Z:/definitely/missing.mp4')
  await page.getByRole('button', { name: '开始监控' }).click()

  // 后端会把「视频不存在」之类的原因透传回来；状态落到失败
  const alert = page.getByRole('alert')
  await expect(alert).toBeVisible({ timeout: 15_000 })
  await expect(alert).toContainText(/不存在|无法|失败/)
  await expect(page.getByText('失败')).toBeVisible()
})
