import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { App } from './App'

describe('App', () => {
  it('renders the QuotePilot application frame while session state is restored', () => {
    const markup = renderToStaticMarkup(<App />)

    expect(markup).toContain('QuotePilot')
    expect(markup).toContain('Quotation preparation workspace')
    expect(markup).toContain('Restoring session')
    expect(markup).toContain('state-panel--loading')
  })
})
