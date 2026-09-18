import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { Button, StatePanel, StatusBadge, TextField } from './ui'

describe('generic UI primitives', () => {
  it('renders explicit loading and error states with accessible live semantics', () => {
    const loading = renderToStaticMarkup(
      <StatePanel
        kind="loading"
        title="Loading records"
        message="Records are being loaded."
      />,
    )
    const error = renderToStaticMarkup(
      <StatePanel
        kind="error"
        title="Could not load records"
        message="Retry when the service is available."
        action={<Button variant="secondary">Retry</Button>}
      />,
    )

    expect(loading).toContain('role="status"')
    expect(loading).toContain('aria-busy="true"')
    expect(loading).toContain('Loading records')
    expect(error).toContain('role="alert"')
    expect(error).toContain('Could not load records')
    expect(error).toContain('Retry')
  })

  it('associates persistent field labels and textual validation errors', () => {
    const markup = renderToStaticMarkup(
      <TextField
        id="quantity"
        name="quantity"
        label="Quantity"
        error="Enter a quantity greater than zero."
      />,
    )

    expect(markup).toContain('for="quantity"')
    expect(markup).toContain('aria-invalid="true"')
    expect(markup).toContain('aria-describedby="quantity-error"')
    expect(markup).toContain('Enter a quantity greater than zero.')
  })

  it('renders status text and button variants without color-only meaning', () => {
    const markup = renderToStaticMarkup(
      <div>
        <StatusBadge tone="warning">Needs attention</StatusBadge>
        <Button variant="danger">Disable</Button>
      </div>,
    )

    expect(markup).toContain('Needs attention')
    expect(markup).toContain('status-badge--warning')
    expect(markup).toContain('button--danger')
    expect(markup).toContain('Disable')
  })
})
