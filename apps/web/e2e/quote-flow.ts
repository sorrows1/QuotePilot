import { expect, type Page, type TestInfo } from '@playwright/test'

export async function quoteFlow(admin: Page, sales: Page, info: TestInfo) {
  await admin.getByRole('button', { name: 'Import another file', exact: true }).click()
  const datasets = [
    ['products', 'sku,name,description\nQUOTE-TEST,Quote test valve,Test valve\n'],
    ['pricebooks', 'key,version,valid_from,source\nQUOTE-BOOK,1,2020-01-01T00:00:00Z,e2e\n'],
    ['prices', 'sku,pricebook_key,pricebook_version,uom,unit_price,quantity_min,valid_from,source\nQUOTE-TEST,QUOTE-BOOK,1,EA,10,0.000001,2020-01-01T00:00:00Z,e2e\n'],
    ['pricebook_assignments', 'pricebook_key,pricebook_version,valid_from,source\nQUOTE-BOOK,1,2020-01-01T00:00:00Z,e2e\n'],
  ]
  for (const [kind, csv] of datasets) {
    await admin.getByLabel('Data type', { exact: true }).selectOption(kind)
    await admin.getByLabel('CSV or XLSX file').setInputFiles({ name: `${kind}.csv`, mimeType: 'text/csv', buffer: Buffer.from(csv) })
    await admin.getByRole('button', { name: 'Upload file', exact: true }).click()
    await admin.getByRole('button', { name: 'Validate preview', exact: true }).click()
    await expect(admin.getByText('Ready to commit', { exact: true })).toBeVisible()
    await admin.getByRole('button', { name: 'Commit entire file', exact: true }).click()
    await expect(admin.getByText('Import committed', { exact: true })).toBeVisible()
    await admin.getByRole('button', { name: 'Import another file', exact: true }).click()
  }
  const errors: string[] = []
  sales.on('pageerror', (error) => errors.push(error.message))
  await sales.reload()
  await sales.getByText('Create customer', { exact: true }).click()
  await sales.getByLabel('External customer key', { exact: true }).fill('SYN-CUST-100')
  await sales.getByLabel('Customer name', { exact: true }).fill('Name must not overwrite imported authority')
  await sales.getByRole('button', { name: 'Create and select customer', exact: true }).click()
  await expect(
    sales.getByLabel('Customer for new draft', { exact: true }).locator('option:checked'),
  ).toHaveText('SYN-CUST-100 · Synthetic Industrial Supplies')
  await sales.getByRole('button', { name: 'New draft', exact: true }).click()
  await expect(sales.getByRole('heading', { name: 'Manual quote workspace' })).toBeFocused()
  await sales.getByLabel('Active product', { exact: true }).selectOption({ label: 'QUOTE-TEST · Quote test valve' })
  await sales.getByRole('button', { name: 'Add line', exact: true }).click()
  await sales.getByLabel('Quantity', { exact: true }).fill('2')
  await sales.getByLabel('Quote UOM', { exact: true }).fill('EA')
  await sales.getByLabel('Propose negotiated unit price', { exact: true }).check()
  await sales.getByLabel('Proposed unit price', { exact: true }).fill('9.50')
  await sales.getByLabel('Proposal reason', { exact: true }).fill('Package concession')
  await sales.route('**/api/customers', async (route) => {
    await route.fulfill({
      status: 409,
      contentType: 'application/json',
      body: JSON.stringify({
        code: 'CUSTOMER_EXTERNAL_KEY_CONFLICT',
        message: 'Customer external key is reserved by an archived customer.',
      }),
    })
  }, { times: 1 })
  await sales.getByLabel('External customer key', { exact: true }).fill('ARCHIVED-CUSTOMER')
  await sales.getByLabel('Customer name', { exact: true }).fill('Archived customer')
  await sales.getByRole('button', { name: 'Create and select customer', exact: true }).click()
  await expect(sales.getByRole('alert')).toContainText('reserved by an archived customer')
  await expect(sales.getByText('Concurrent edit conflict', { exact: true })).toHaveCount(0)
  await expect(sales.getByRole('button', { name: 'Reload latest (replaces local edits)' })).toHaveCount(0)
  await expect(sales.getByLabel('Quantity', { exact: true })).toBeEnabled()
  await expect(sales.getByLabel('Quantity', { exact: true })).toHaveValue('2')
  await sales.getByLabel('Freight (SGD)', { exact: true }).fill('1.001')
  await sales.getByRole('button', { name: 'Calculate', exact: true }).click()
  await expect(sales.getByRole('alert')).toContainText('Correct the highlighted quote fields')
  await expect(sales.getByText(
    'Enter a nonnegative amount with at most two decimal places.',
    { exact: true },
  )).toBeVisible()
  await expect(sales.getByLabel('Freight (SGD)', { exact: true })).toHaveAttribute('aria-invalid', 'true')
  await expect(sales.getByLabel('Freight (SGD)', { exact: true })).toBeFocused()
  await expect(sales.getByLabel('Quantity', { exact: true })).toHaveValue('2')
  await expect(sales.getByLabel('Proposed unit price', { exact: true })).toHaveValue('9.50')
  await sales.getByLabel('Freight (SGD)', { exact: true }).fill('0.00')
  await sales.getByRole('button', { name: 'Calculate', exact: true }).click()
  await expect(sales.getByText('approval required · provisional', { exact: true })).toBeVisible()
  await sales.route('**/api/quotes/*/revisions', async (route) => {
    expect((await route.fetch()).status()).toBe(201)
    await route.abort('failed')
  }, { times: 1 })
  await sales.getByRole('button', { name: 'Save new revision', exact: true }).click()
  await expect(sales.getByRole('alert')).toBeVisible()
  await sales.getByRole('button', { name: 'Save new revision', exact: true }).click()
  await expect(sales.getByText(/saved revision 1/)).toBeVisible()
  await sales.reload()
  await sales.getByLabel('Open saved draft', { exact: true }).selectOption({ index: 1 })
  await expect(sales.getByLabel('Quantity', { exact: true })).toHaveValue('2')
  await expect(sales.getByText(/Normal reference: 10.*Negotiated proposal: 9.50/).first()).toBeVisible()
  await sales.getByRole('button', { name: 'Preview draft', exact: true }).click()
  await expect(sales.getByRole('region', { name: 'Draft preview' })).toContainText('Draft / provisional')
  await sales.screenshot({ path: info.outputPath('quote-desktop.png'), fullPage: true })
  await sales.setViewportSize({ width: 760, height: 900 })
  expect(await sales.locator('body').evaluate(el => el.scrollWidth <= window.innerWidth)).toBeTruthy()
  await sales.screenshot({ path: info.outputPath('quote-narrow.png'), fullPage: true })
  expect(await sales.locator('vite-error-overlay').count()).toBe(0)
  expect(errors).toEqual([])
}


