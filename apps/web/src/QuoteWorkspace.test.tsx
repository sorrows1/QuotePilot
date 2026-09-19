import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { QuoteWorkspace, ResultView, type Result } from './QuoteWorkspace'

const result: Result = {
  state: 'CALCULATED', subtotal: '19.00', freight: '1.00', tax: '0.00', total: '20.00',
  settings_revision: 1, pricing_as_of: '2026-09-19T00:00:00Z', commercial_fingerprint: 'test',
  findings: [], evidence: [], lines: [{ line_id: 'L1', normal_reference_unit_price: '10.00',
    negotiated_unit_price: '9.50', line_net: '19.00', inventory_state: 'STALE_INVENTORY',
    aggregate_available: null, inventory_uom: null, evidence: [] }],
}

describe('Quote workspace presentation', () => {
  it('shows a loading state before tenant data arrives', () => {
    expect(renderToStaticMarkup(<QuoteWorkspace csrf="test" />)).toContain('Loading drafts')
  })
  it.each(['CALCULATED', 'HARD_BLOCK', 'CLARIFICATION_REQUIRED', 'APPROVAL_REQUIRED'])(
    'keeps %s provisional and exposes its findings', (state) => {
      const markup = renderToStaticMarkup(<ResultView result={{ ...result, state, findings: [
        { code: 'MISSING_PRICE', kind: 'hard_block', line_id: 'L1' },
        { code: 'NEGOTIATED_UNIT_PRICE', kind: 'approval', line_id: 'L1' },
      ] }} />)
      expect(markup).toContain('provisional')
      expect(markup).toContain('missing price')
      expect(markup).toContain('negotiated unit price')
      expect(markup).toContain('Normal reference: 10.00')
      expect(markup).toContain('Negotiated proposal: 9.50')
      expect(markup).toContain('stale inventory')
      expect(markup).not.toMatch(/READY|approved|Generate|Send/)
    },
  )
  it('does not present absent totals as zero', () => {
    const markup = renderToStaticMarkup(<ResultView result={{ ...result, total: null, subtotal: null }} />)
    expect(markup).toContain('Incomplete — resolve findings')
  })
})
