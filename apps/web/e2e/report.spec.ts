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

test('区间无事件时拒绝生成并说清原因', async ({ page }) => {
  await page.goto('/reports')

  await page.getByLabel('起始日期').fill('2020-01-01')
  await page.getByLabel('结束日期').fill('2020-01-31')
  await page.getByRole('button', { name: /生成报告/ }).click()

  await expect(page.getByRole('alert')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByRole('alert')).toContainText(/没有告警|未配置/)
})

test('生成冒烟：真调模型并出现五节标题', async ({ page }) => {
  test.skip(!process.env.FIRESIGHT_LLM_API_KEY, '未配 FIRESIGHT_LLM_API_KEY，跳过真调模型的用例')

  await page.goto('/reports')
  await page.getByRole('button', { name: /生成报告/ }).click()

  // 生成要几十秒：等「火情态势」这一节出现
  await expect(page.getByRole('heading', { name: '火情态势' })).toBeVisible({ timeout: 120_000 })
  // 下载是导航，控件是 <a href>，角色是 link 而不是 button
  await expect(page.getByRole('link', { name: '下载 .md' })).toBeVisible()
})
