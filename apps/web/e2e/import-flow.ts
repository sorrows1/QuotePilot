import { expect, type Page, type TestInfo } from '@playwright/test'
import { fileURLToPath } from 'node:url'

export async function importFlow(page: Page, testInfo: TestInfo) {
  const imports = page.getByRole('region', { name: 'Imports', exact: true })
  await expect(imports.getByText('Upload → Map columns → Review → Commit.', { exact: false })).toBeVisible()
  await imports.getByLabel('CSV or XLSX file').setInputFiles({
    name: 'invalid-customers.csv', mimeType: 'text/csv',
    buffer: Buffer.from('external_key,name\n,Missing customer key\n'),
  })
  await imports.getByRole('button', { name: 'Upload file', exact: true }).click()
  await expect(imports.getByText('invalid-customers.csv', { exact: true })).toBeVisible()
  await imports.getByRole('button', { name: 'Validate preview', exact: true }).click()
  await expect(imports.getByRole('alert')).toContainText('File has errors')
  await expect(imports.getByRole('button', { name: 'Commit entire file' })).toBeDisabled()
  const downloadEvent = page.waitForEvent('download')
  await imports.getByRole('link', { name: 'Download row errors' }).click()
  expect((await downloadEvent).suggestedFilename()).toBe('import-errors.csv')
  await imports.getByLabel('CSV or XLSX file').setInputFiles({
    name: 'customers.csv', mimeType: 'text/csv',
    buffer: Buffer.from('Customer Code,Display Name\nSYN-CUST-100,Synthetic Industrial Supplies\nSYN-CUST-200,Synthetic Maintenance Services\n'),
  })
  await imports.getByRole('button', { name: 'Upload file', exact: true }).click()
  await expect(imports.getByText('customers.csv', { exact: true })).toBeVisible()
  await imports.getByLabel('external key (required)', { exact: true }).selectOption('Customer Code')
  await imports.getByLabel('name (required)', { exact: true }).selectOption('Display Name')
  await imports.getByRole('button', { name: 'Validate preview', exact: true }).click()
  await expect(imports.getByText('Ready to commit', { exact: true })).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.locator('body').evaluate(el => el.scrollWidth <= window.innerWidth)).toBeTruthy()
  await imports.screenshot({ path: testInfo.outputPath('imports-mobile-preview.png') })
  await page.setViewportSize({ width: 1280, height: 900 })
  await page.route('**/api/admin/imports/*/commit', async route => {
    const response = await route.fetch()
    expect(response.status()).toBe(200)
    await route.abort('failed') // Server committed, but the browser did not receive its receipt.
  }, { times: 1 })
  await imports.getByRole('button', { name: 'Commit entire file' }).click()
  await expect(imports.getByRole('button', { name: 'Retry same commit' })).toBeVisible()
  await expect(imports.getByRole('button', { name: 'Upload file' })).toBeDisabled()
  await imports.getByRole('button', { name: 'Retry same commit' }).click()
  await expect(imports.getByText('Import committed', { exact: true })).toBeVisible()
  await expect(imports.getByText('2 created · 0 replaced · 0 skipped', { exact: true })).toBeVisible()
  await imports.screenshot({ path: testInfo.outputPath('imports-desktop-committed.png') })
  await imports.getByRole('button', { name: 'Refresh history' }).click()
  await imports.getByRole('button', { name: 'customers.csv · committed', exact: true }).click()
  await expect(imports.getByText('Import committed', { exact: true })).toBeVisible()
  await imports.getByRole('button', { name: 'Import another file', exact: true }).click()
  await imports.getByLabel('CSV or XLSX file').setInputFiles(fileURLToPath(
    new URL('../../server/tests/fixtures/imports/customers.xlsx', import.meta.url),
  ))
  await imports.getByRole('button', { name: 'Upload file', exact: true }).click()
  await expect(imports.getByText('customers.xlsx', { exact: true })).toBeVisible()
  await imports.getByLabel('Existing records', { exact: true }).selectOption('skip_identical')
  await imports.getByRole('button', { name: 'Validate preview', exact: true }).click()
  await expect(imports.getByText('Ready to commit', { exact: true })).toBeVisible()
  await imports.getByRole('button', { name: 'Commit entire file', exact: true }).click()
  await expect(imports.getByText('0 created · 0 replaced · 2 skipped', { exact: true })).toBeVisible()
}
