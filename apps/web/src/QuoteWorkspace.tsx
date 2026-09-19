import { useEffect, useRef, useState, type FormEvent } from 'react'
import { api, ApiError } from './api'
import { Button, SelectField, StatePanel, StatusBadge, TextField } from './ui'

type Choice = { id: string; name: string; sku?: string }
type Line = {
  line_id: string; product_id: string; quantity: string; quote_uom: string; pricing_uom: string
  availability_required: boolean; substitute_for?: string | null
  negotiated: { unit_price: string; reason: string } | null
}
type Candidate = { customer_id: string; lines: Line[]; freight: string }
type Finding = { code: string; kind: string; line_id: string | null }
export type Result = {
  state: string; subtotal: string | null; freight: string; tax: string | null; total: string | null
  settings_revision: number; pricing_as_of: string; commercial_fingerprint: string
  findings: Finding[]; evidence: { kind: string; record_json: string }[]
  lines: {
    line_id: string; normal_reference_unit_price: string | null; negotiated_unit_price: string | null
    line_net: string | null; inventory_state: string; aggregate_available: string | null; inventory_uom: string | null
    evidence: { kind: string; record_json: string }[]
  }[]
}
type Revision = {
  id: string; revision: number; created_at: string; inputs: Candidate; result: Result; settings_stale: boolean
  exception_set: { id: string; members: { id: string; code: string; line_id: string | null }[] }
}
type Case = { id: string; version: number; created_at: string; revisions: Revision[] }
const empty: Candidate = { customer_id: '', lines: [], freight: '0.00' }
const readable = (text: string) => text.replaceAll('_', ' ').toLowerCase()

function capturedName(evidence: Result['evidence'], kind: string) {
  const record = evidence.find((item) => item.kind === kind)
  if (!record) return 'Unavailable'
  const value: unknown = JSON.parse(record.record_json)
  return value && typeof value === 'object' && 'name' in value && typeof value.name === 'string' ? value.name : 'Unavailable'
}

function editable(inputs: Candidate): Candidate {
  return {
    customer_id: inputs.customer_id, freight: inputs.freight,
    lines: inputs.lines.map((line) => ({
      line_id: line.line_id, product_id: line.product_id, quantity: line.quantity,
      quote_uom: line.quote_uom, pricing_uom: line.pricing_uom,
      availability_required: line.availability_required, substitute_for: line.substitute_for,
      negotiated: line.negotiated ? { unit_price: line.negotiated.unit_price, reason: line.negotiated.reason } : null,
    })),
  }
}

export function QuoteWorkspace({ csrf }: { csrf: string }) {
  const [cases, setCases] = useState<Case[]>([])
  const [customers, setCustomers] = useState<Choice[]>([])
  const [products, setProducts] = useState<Choice[]>([])
  const [draft, setDraft] = useState<Case | null>(null)
  const [candidate, setCandidate] = useState<Candidate>(empty)
  const [result, setResult] = useState<Result | null>(null)
  const [saved, setSaved] = useState<Revision | null>(null)
  const [dirty, setDirty] = useState(false)
  const [preview, setPreview] = useState(false)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState(false)
  const [customerSearch, setCustomerSearch] = useState('')
  const [productSearch, setProductSearch] = useState('')
  const [newCustomer, setNewCustomer] = useState('')
  const [product, setProduct] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  const retry = useRef<{ signature: string; key: string } | null>(null)

  async function mutate<T>(path: string, payload: object): Promise<T> {
    const signature = JSON.stringify([path, payload])
    if (retry.current?.signature !== signature) retry.current = { signature, key: crypto.randomUUID() }
    const response = await api<T>(path, 'POST', { ...payload, request_key: retry.current.key }, csrf)
    retry.current = null
    return response
  }

  async function run(action: () => Promise<void>) {
    setBusy(true); setError('')
    try { await action() } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Service unavailable. Try again.')
      if (caught instanceof ApiError && caught.status === 409) setConflict(true)
    } finally { setBusy(false) }
  }

  useEffect(() => {
    let active = true
    Promise.all([api<Case[]>('/api/quotes'), api<Choice[]>('/api/customers'), api<Choice[]>('/api/products')])
      .then(([list, people, items]) => { if (active) { setCases(list); setCustomers(people); setProducts(items) } })
      .catch((caught: unknown) => { if (active) setError(caught instanceof Error ? caught.message : 'Unable to load workspace.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  function display(next: Case) {
    const latest = next.revisions[0] ?? null
    setDraft(next); setCandidate(latest ? editable(latest.inputs) : empty)
    setSaved(latest); setResult(latest?.result ?? null); setDirty(false); setConflict(false); setPreview(false)
    requestAnimationFrame(() => heading.current?.focus())
  }

  function edit(next: Candidate) {
    setCandidate(next); setDirty(true); setResult(null); setPreview(false)
  }

  function changeLine(index: number, change: Partial<Line>) {
    edit({ ...candidate, lines: candidate.lines.map((line, i) => i === index ? { ...line, ...change } : line) })
  }

  async function search(kind: 'customers' | 'products') {
    const query = kind === 'customers' ? customerSearch : productSearch
    const choices = await api<Choice[]>(`/api/${kind}?q=${encodeURIComponent(query)}`)
    if (kind === 'customers') setCustomers(choices)
    else setProducts(choices)
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!draft) return
    const action = (event.nativeEvent as SubmitEvent).submitter?.getAttribute('value')
    void run(async () => {
      if (action === 'save') {
        const response = await mutate<{ version: number; revision: Revision }>(`/api/quotes/${draft.id}/revisions`, {
          ...candidate, expected_version: draft.version,
        })
        setDraft({ ...draft, version: response.version, revisions: [response.revision, ...draft.revisions] })
        setSaved(response.revision); setResult(response.revision.result); setDirty(false)
        setCases(await api<Case[]>('/api/quotes'))
      } else {
        setResult(await api<Result>(`/api/quotes/${draft.id}/calculate`, 'POST', candidate, csrf))
        setDirty(true)
      }
    })
  }

  if (loading) return <StatePanel kind="loading" title="Loading drafts" message="Loading your company’s quote workspace." />

  return <section className="content-section quote-workspace" aria-labelledby="quote-title">
    <div className="section-heading">
      <h2 id="quote-title" tabIndex={-1} ref={heading}>Manual quote workspace</h2>
      <p>Prepare a draft from current commercial records. Every save captures a new revision.</p>
    </div>
    {error ? <StatePanel kind="error" title={conflict ? 'Concurrent edit conflict' : 'Action needed'} message={error}
      action={conflict && draft ? <Button variant="secondary" disabled={busy} onClick={() => void run(async () => display(await api<Case>(`/api/quotes/${draft.id}`)))}>Reload latest (replaces local edits)</Button> : undefined} /> : null}
    <fieldset disabled={busy}>
      <legend>Draft cases</legend>
      <div className="actions">
        <Button variant="secondary" onClick={() => void run(async () => {
          const created = await mutate<Case>('/api/quotes', {})
          display(created); setCases(await api<Case[]>('/api/quotes'))
        })} disabled={dirty}>New draft</Button>
        <Button variant="secondary" onClick={() => void run(async () => setCases(await api<Case[]>('/api/quotes')))}>Refresh drafts</Button>
      </div>
      {dirty ? <p>Save your edits before switching drafts.</p> : null}
      <SelectField id="open-draft" name="open-draft" label="Open saved draft" value={draft?.id ?? ''} disabled={dirty}
        onChange={(e) => { const id = e.target.value; if (id) void run(async () => display(await api<Case>(`/api/quotes/${id}`))) }}>
        <option value="">Select a draft</option>
        {cases.map((item) => <option key={item.id} value={item.id}>{item.id.slice(0, 8)} · revision {item.version}</option>)}
      </SelectField>
    </fieldset>
    {!draft ? <StatePanel kind="empty" title="Choose or create a draft" message="Start with a customer, then select products and quantities." /> : <>
      <div className="quote-context"><StatusBadge tone="warning">Draft / provisional</StatusBadge>
        <p>Internal case {draft.id} · saved revision {draft.version}</p>
        <p>{dirty ? 'Unsaved inputs or calculation' : saved ? 'Showing captured saved results' : 'Empty draft'}</p>
      </div>
      {saved?.settings_stale ? <StatePanel kind="info" title="Settings changed — revalidation required" message="This saved revision retains its original evidence. Calculate and save a new revision using current settings." /> : null}
      <fieldset disabled={busy}>
        <legend>Customer</legend>
        <TextField id="customer-search" name="customer-search" label="Find customer" value={customerSearch} onChange={(e) => setCustomerSearch(e.target.value)} />
        <Button variant="secondary" onClick={() => void run(() => search('customers'))}>Find customers</Button>
        <SelectField id="quote-customer" name="quote-customer" label="Selected customer" value={candidate.customer_id}
          onChange={(e) => edit({ ...candidate, customer_id: e.target.value })}>
          <option value="">Select customer</option>
          {candidate.customer_id && !customers.some((c) => c.id === candidate.customer_id) ? <option value={candidate.customer_id}>Saved customer {candidate.customer_id}</option> : null}
          {customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </SelectField>
        <details><summary>Create customer</summary>
          <TextField id="new-customer" name="new-customer" label="Customer name" maxLength={255} value={newCustomer} onChange={(e) => setNewCustomer(e.target.value)} />
          <Button variant="secondary" disabled={!newCustomer.trim()} onClick={() => void run(async () => {
            const created = await mutate<Choice>('/api/customers', { name: newCustomer })
            setCustomers([...customers, created]); edit({ ...candidate, customer_id: created.id }); setNewCustomer('')
          })}>Create and select customer</Button>
        </details>
      </fieldset>
      <div className="quote-compact" aria-live="polite">Draft total: {result?.total ?? 'Not calculated'} {result?.total ? 'SGD' : ''} · {result ? readable(result.state) : 'Choose inputs and calculate'} · {conflict ? 'Reload latest to reconcile edits' : result ? 'Next: review findings and save draft' : 'Next: complete lines and calculate'}</div>
      <fieldset disabled={busy}>
        <legend>Add products</legend>
        <TextField id="product-search" name="product-search" label="Find product by SKU or name" value={productSearch} onChange={(e) => setProductSearch(e.target.value)} />
        <Button variant="secondary" onClick={() => void run(() => search('products'))}>Find products</Button>
        <SelectField id="add-product" name="add-product" label="Active product" value={product} onChange={(e) => setProduct(e.target.value)}>
          <option value="">Select product</option>{products.map((p) => <option key={p.id} value={p.id}>{p.sku} · {p.name}</option>)}
        </SelectField>
        {!products.length ? <p>No matching active products. Change the lookup or contact your administrator.</p> : null}
        <Button variant="secondary" disabled={!products.some((p) => p.id === product)} onClick={() => {
          edit({ ...candidate, lines: [...candidate.lines, { line_id: crypto.randomUUID(), product_id: product, quantity: '1', quote_uom: '', pricing_uom: '', availability_required: false, negotiated: null }] })
          setProduct('')
        }}>Add line</Button>
      </fieldset>
      <form onSubmit={submit}>
        <fieldset disabled={busy || conflict}>
          <legend>Requested lines</legend>
          {!candidate.lines.length ? <StatePanel kind="empty" title="No requested lines" message="Select an active product to add your first line." /> : null}
          {candidate.lines.map((line, index) => <section className="quote-line" key={line.line_id} aria-label={`Line ${index + 1}`}>
            <h3>Line {index + 1} · {products.find((p) => p.id === line.product_id)?.name ?? line.product_id}</h3>
            <div className="quote-fields">
              <TextField id={`qty-${index}`} name={`qty-${index}`} label="Quantity" inputMode="decimal" required value={line.quantity} onChange={(e) => changeLine(index, { quantity: e.target.value })} />
              <TextField id={`uom-${index}`} name={`uom-${index}`} label="Quote UOM" required pattern="[A-Z][A-Z0-9_]*" maxLength={30} value={line.quote_uom} onChange={(e) => changeLine(index, { quote_uom: e.target.value })} />
              <TextField id={`pricing-uom-${index}`} name={`pricing-uom-${index}`} label="Pricing UOM" help="Use the unit on the authoritative price record." required pattern="[A-Z][A-Z0-9_]*" maxLength={30} value={line.pricing_uom} onChange={(e) => changeLine(index, { pricing_uom: e.target.value })} />
            </div>
            <label><input type="checkbox" checked={line.availability_required} onChange={(e) => changeLine(index, { availability_required: e.target.checked })} /> Require current availability evidence</label>
            <label><input type="checkbox" checked={!!line.negotiated} onChange={(e) => changeLine(index, { negotiated: e.target.checked ? { unit_price: '', reason: '' } : null })} /> Propose negotiated unit price</label>
            {line.negotiated ? <div className="quote-fields">
              <TextField id={`proposal-${index}`} name={`proposal-${index}`} label="Proposed unit price" help="Provisional proposal; normal pricebook authority remains unchanged." required inputMode="decimal" value={line.negotiated.unit_price} onChange={(e) => changeLine(index, { negotiated: { ...line.negotiated!, unit_price: e.target.value } })} />
              <TextField id={`reason-${index}`} name={`reason-${index}`} label="Proposal reason" required maxLength={1000} value={line.negotiated.reason} onChange={(e) => changeLine(index, { negotiated: { ...line.negotiated!, reason: e.target.value } })} />
            </div> : null}
            <Button type="button" variant="secondary" onClick={() => edit({ ...candidate, lines: candidate.lines.filter((_, i) => i !== index) })}>Remove line {index + 1}</Button>
          </section>)}
          <TextField id="freight" name="freight" label="Freight (SGD)" inputMode="decimal" required value={candidate.freight} onChange={(e) => edit({ ...candidate, freight: e.target.value })} />
          <div className="quote-summary">
            <h3>Draft summary</h3>
            {result ? <ResultView result={result} inputs={candidate} /> : <p>Choose a customer and at least one line, then calculate current prices and stock context.</p>}
            <div className="actions">
              <Button type="submit" value="calculate" variant={result ? 'secondary' : 'primary'} disabled={!candidate.customer_id || !candidate.lines.length} busy={busy}>Calculate</Button>
              <Button type="submit" value="save" variant={result ? 'primary' : 'secondary'} disabled={!candidate.customer_id || !candidate.lines.length} busy={busy}>Save new revision</Button>
            </div>
            <p>Saving recalculates on the server. Exceptions remain unresolved; all results are provisional.</p>
          </div>
        </fieldset>
      </form>
      {result ? <Button variant="secondary" onClick={() => setPreview(!preview)}>{preview ? 'Close preview' : 'Preview draft'}</Button> : null}
      {preview && result ? <section className="quote-preview" aria-label="Draft preview"><h3>Draft / provisional — preview</h3><p>Internal case {draft.id.slice(0, 8)}. {dirty ? 'Unsaved calculation.' : `Captured revision ${saved?.revision}.`}</p><ResultView result={result} inputs={candidate} /></section> : null}
      <details><summary>Saved evidence and revision history ({draft.revisions.length})</summary>
        {draft.revisions.map((revision) => <details key={revision.id}>
          <summary>Revision {revision.revision} · {new Date(revision.created_at).toLocaleString()}</summary>
          <ResultView result={revision.result} inputs={revision.inputs} />
          <p>Exception set {revision.exception_set.id} · {revision.exception_set.members.length} unresolved approval findings</p>
          <ul>{revision.exception_set.members.map((m) => <li key={m.id}>{readable(m.code)} · {m.line_id ?? 'Quote'} · {m.id}</li>)}</ul>
          <pre>{JSON.stringify({ inputs: revision.inputs, evidence: revision.result.evidence, lines: revision.result.lines, fingerprint: revision.result.commercial_fingerprint }, null, 2)}</pre>
        </details>)}
      </details>
    </>}
  </section>
}

export function ResultView({ result, inputs }: { result: Result; inputs?: Candidate }) {
  return <div className="quote-result">
    <p>Captured customer: {capturedName(result.evidence, 'customer')}</p>
    <StatusBadge tone={result.state === 'HARD_BLOCK' ? 'danger' : 'warning'}>{readable(result.state)} · provisional</StatusBadge>
    {result.findings.length ? <ul>{result.findings.map((f, i) => <li key={i}>{readable(f.kind)}: {readable(f.code)} {f.line_id ? `· line ${Math.max(0, result.lines.findIndex((line) => line.line_id === f.line_id)) + 1}` : ''}</li>)}</ul> : null}
    {result.lines.map((line, i) => <div key={line.line_id} className="quote-line-result">
      <strong>Line {i + 1} · {capturedName(line.evidence, 'product')}</strong>
      {inputs ? <p>Quantity: {inputs.lines.find((input) => input.line_id === line.line_id)?.quantity} {inputs.lines.find((input) => input.line_id === line.line_id)?.quote_uom}</p> : null}
      <p>Normal reference: {line.normal_reference_unit_price ?? 'Unavailable'} · Negotiated proposal: {line.negotiated_unit_price ?? 'None'} · Line net: {line.line_net ?? 'Unavailable'}</p>
      <p>Inventory: {readable(line.inventory_state)}{line.aggregate_available !== null ? ` · ${line.aggregate_available} ${line.inventory_uom}` : ''}</p>
    </div>)}
    <dl className="quote-totals"><dt>Subtotal</dt><dd>{result.subtotal ?? 'Unavailable'}</dd><dt>Freight</dt><dd>{result.freight}</dd><dt>Tax</dt><dd>{result.tax ?? 'Unavailable'}</dd><dt>Draft total (SGD)</dt><dd>{result.total ?? 'Incomplete — resolve findings'}</dd></dl>
    <p>Settings revision {result.settings_revision} · calculated {new Date(result.pricing_as_of).toLocaleString()}</p>
  </div>
}
