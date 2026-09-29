import { expect, test } from '@playwright/test'

/** 分析报告页：结构、拒绝条件、生成冒烟（真调模型，仅在配了 key 时跑）。 */

test('报告页结构：时间选择、生成按钮、历史区都在', async ({ page }) => {
  await page.goto('/reports')

  await expect(page.getByRole('heading', { name: '分析报告' })).toBeVisible()
  await expect(page.getByLabel('起始日期')).toBeVisible()
  await expect(page.getByLabel('结束日期')).toBeVisible()
  await expect(page.getByRole('button', { name: /生成报告|生成中/ })).toBeVisible()
  await expect(page.getByText('历史报告')).toBeVisible()
})

test('模型输出的 Markdown 过消毒后再进 v-html', async ({ page }) => {
  // 报告正文是模型输出（还掺着事件的 location / message），按不可信内容处理：
  // 直接喂注入载荷，验证 <script>/onerror/javascript: 都进不了文档
  const malicious = [
    '## 火情态势',
    '<script>window.__pwned = "script"</script>',
    '<img src=x onerror="window.__pwned = \'onerror\'">',
    '<a href="javascript:window.__pwned = \'js-url\'">点我</a>',
  ].join('\n')

  await page.route('**/api/reports/rep-xss', (route) =>
    route.fulfill({
      json: {
        meta: {
          id: 'rep-xss',
          title: '注入测试报告',
          start: '2026-03-01',
          end: '2026-03-31',
          created_at: '2026-09-29T10:00:00',
          model: 'test-model',
        },
        markdown: malicious,
      },
    }),
  )
  await page.route('**/api/reports', (route) =>
    route.fulfill({
      json: [
        {
          id: 'rep-xss',
          title: '注入测试报告',
          start: '2026-03-01',
          end: '2026-03-31',
          created_at: '2026-09-29T10:00:00',
          model: 'test-model',
        },
      ],
    }),
  )

  await page.goto('/reports')
  await page.getByRole('button', { name: '注入测试报告' }).click()

  // 正常的 Markdown 还是要渲染出来的
  await expect(page.getByRole('heading', { name: '火情态势' })).toBeVisible()
  // 注入的东西一条都不该活下来
  expect(await page.evaluate(() => (window as unknown as { __pwned?: string }).__pwned)).toBeUndefined()
  await expect(page.locator('article.md script')).toHaveCount(0)
  await expect(page.locator('article.md [onerror]')).toHaveCount(0)
  await expect(page.locator('article.md a[href^="javascript:"]')).toHaveCount(0)
})

test('区间无事件时拒绝生成并说清原因', async ({ page }) => {
  // 前提：真实事件库的告警都在近期，2020-01 这段没有事件（走「没有告警」分支）；
  // 区间真有了事件则落到「未配置」分支，正则两个分支都接得住
  await page.goto('/reports')

  await page.getByLabel('起始日期').fill('2020-01-01')
  await page.getByLabel('结束日期').fill('2020-01-31')
  await page.getByRole('button', { name: /生成报告/ }).click()

  await expect(page.getByRole('alert')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('alert')).toContainText(/没有告警|未配置/)
})

test('生成冒烟：真调模型并出现五节标题', async ({ page }) => {
  // 整条用例预算 120 秒：playwright.config 的 60 秒会先掐掉整条用例，
  // 只给 expect 更大的 timeout 没用（生成本身要几十秒，60~120 秒就会误报超时）
  test.setTimeout(120_000)
  test.skip(!process.env.FIRESIGHT_LLM_API_KEY, '未配 FIRESIGHT_LLM_API_KEY，跳过真调模型的用例')

  await page.goto('/reports')
  await page.getByRole('button', { name: /生成报告/ }).click()

  // 生成要几十秒：等「火情态势」这一节出现
  await expect(page.getByRole('heading', { name: '火情态势' })).toBeVisible({ timeout: 120_000 })
  // 下载是导航，控件是 <a href>，角色是 link 而不是 button；href 必须带 download=1
  const link = page.getByRole('link', { name: '下载 .md' })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', /download=1/)
})
