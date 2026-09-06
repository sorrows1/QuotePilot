import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { App } from './App'

describe('App', () => {
  it('renders the QuotePilot foundation shell', () => {
    const markup = renderToStaticMarkup(<App />)

    expect(markup).toContain('QuotePilot')
    expect(markup).toContain('Foundation ready')
  })
})
