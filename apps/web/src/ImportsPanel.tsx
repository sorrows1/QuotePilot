import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'
import { Button, SelectField, StatePanel } from './ui'

type Summary = { created: number; updated: number; skipped: number }
type Result = Summary & { job_id: string; status: 'committed' }
type Job = {
  id: string; filename: string; kind: string; headers: string[]; row_count: number
  sample: string[][]; required_fields: string[]; optional_fields: string[]
  mapping: Record<string, string> | null; mode: string | null; preview_token: string | null
  preview: { valid: boolean; errors: { row: number; field: string; message: string }[]; summary: Summary } | null
  result: Result | null
}
type History = { id: string; filename: string; kind: string; status: string }
const kinds: Record<string, string> = {
  customers: 'Customers', products: 'Products', pricebooks: 'Pricebooks', prices: 'Prices',
  inventory: 'Inventory', pricebook_assignments: 'Pricebook assignments',
  uom_conversions: 'UOM conversions', product_costs: 'Product costs',
}
const label = (field: string) => field.replaceAll('_', ' ')

async function encoded(file: File): Promise<string> {
  if (!file.size || file.size > 2_000_000) throw new Error('Choose a nonempty file of at most 2 MB.')
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192))
  }
  return btoa(binary)
}

function counts(summary: Summary) {
  return `${summary.created} created · ${summary.updated} replaced · ${summary.skipped} skipped`
}

export function ImportsPanel({ csrf }: { csrf: string }) {
  const [kind, setKind] = useState('customers')
  const [job, setJob] = useState<Job | null>(null)
  const [mapping, setMapping] = useState<Record<string, string>>({})
  const [mode, setMode] = useState('insert')
  const [history, setHistory] = useState<History[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [uncertain, setUncertain] = useState(false)
  const [denied, setDenied] = useState(false)
  const fileInput = useRef<HTMLInputElement | null>(null)
  const section = useRef<HTMLElement>(null)
  useEffect(() => {
    let active = true
    api<History[]>('/api/admin/imports').then(items => { if (active) setHistory(items) })
      .catch(() => { if (active) setError('Unable to load import history. Use Refresh history to retry.') })
    return () => { active = false }
  }, [])

  function accept(value: Job) {
    setJob(value)
    setMapping(value.mapping ?? Object.fromEntries(
      [...value.required_fields, ...value.optional_fields].filter(f => value.headers.includes(f)).map(f => [f, f]),
    ))
    setMode(value.mode ?? 'insert')
    setUncertain(false)
  }
  async function run(action: () => Promise<void>) {
    setBusy(true); setError('')
    try { await action() }
    catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Unable to finish. Try again.')
      if (caught instanceof ApiError && [401, 403].includes(caught.status)) {
        setJob(null); setHistory([]); setDenied(true)
      }
    } finally {
      setBusy(false)
      requestAnimationFrame(() => section.current?.focus())
    }
  }
  function invalidate() {
    setJob(previous => previous ? { ...previous, preview: null, preview_token: null } : null)
  }
  const editable = !busy && !uncertain && !job?.result && !denied
  return <section id="imports" ref={section} tabIndex={-1} className="content-section import-panel" aria-labelledby="imports-title">
    <div className="section-heading"><h3 id="imports-title">Imports</h3>
      <p>Upload → Map columns → Review → Commit. Each file is saved in full or not at all.</p>
      <p>Import customers and products first, then pricebooks, prices and inventory. Use exact decimal values and ISO dates with a timezone, such as 2026-09-01T00:00:00Z.</p>
    </div>
    {error ? <StatePanel kind="error" title="Import needs attention" message={error} /> : null}
    {busy ? <StatePanel kind="loading" title="Working" message="Please wait for this operation to finish." /> : null}
    <fieldset disabled={!editable}><legend>1. Upload a file</legend>
      <SelectField id="import-kind" name="import-kind" label="Data type" value={kind} onChange={e => setKind(e.target.value)}>
        {Object.entries(kinds).map(([value, name]) => <option key={value} value={value}>{name}</option>)}
      </SelectField>
      <div className="field"><label htmlFor="import-file">CSV or XLSX file</label>
        <input id="import-file" ref={fileInput} type="file" accept=".csv,.xlsx" />
        <p className="field__help">Maximum 2 MB, 2,000 rows, 40 columns. XLSX: one worksheet, no formulas or external links. Use timezone dates as text.</p>
      </div>
      <Button type="button" onClick={() => void run(async () => {
        const file = fileInput.current?.files?.[0]
        if (!file) throw new Error('Choose a CSV or XLSX file first.')
        accept(await api<Job>('/api/admin/imports', 'POST', { filename: file.name, kind, data_base64: await encoded(file) }, csrf))
      })}>Upload file</Button>
    </fieldset>
    {job ? <>
      <p><strong>{job.filename}</strong> · {kinds[job.kind]} · {job.row_count} rows</p>
      <div className="import-table" tabIndex={0} role="region" aria-label="Uploaded data sample">
        <table><caption>First {job.sample.length} source rows</caption><thead><tr>{job.headers.map(h => <th key={h} scope="col">{h}</th>)}</tr></thead>
          <tbody>{job.sample.map((row, index) => <tr key={index}>{row.map((v, i) => <td key={i}>{v}</td>)}</tr>)}</tbody></table>
      </div>
      {!job.result ? <>
        <fieldset disabled={!editable}><legend>2. Map and validate</legend>
          {[...job.required_fields, ...job.optional_fields].map(field => <SelectField key={field} id={`map-${field}`} name={field}
            label={`${label(field)}${job.required_fields.includes(field) ? ' (required)' : ' (optional)'}`} value={mapping[field] ?? ''}
            onChange={e => { const next = { ...mapping }; if (e.target.value) next[field] = e.target.value; else delete next[field]; setMapping(next); invalidate() }}>
            <option value="">Choose a column</option>{job.headers.map(h => <option key={h} value={h}>{h}</option>)}
          </SelectField>)}
          <SelectField id="import-mode" name="import-mode" label="Existing records" value={mode} onChange={e => { setMode(e.target.value); invalidate() }}>
            <option value="insert">Insert only — reject existing keys</option>
            <option value="skip_identical">Skip identical records — reject conflicting changes</option>
            {['prices', 'product_costs'].includes(job.kind) ? <option value="replace_effective">Replace effective price/cost — preserve earlier history</option> : null}
          </SelectField>
          <p>{['prices', 'product_costs'].includes(job.kind)
            ? 'Replacement closes one earlier open window and creates new authority at the supplied start date. UOM and price tier must match.'
            : 'Existing details cannot be overwritten. Skip identical records to reimport unchanged data; conflicting changes need new keys or non-overlapping authority windows.'}</p>
          <Button type="button" onClick={() => void run(async () => accept(await api<Job>(`/api/admin/imports/${job.id}/preview`, 'POST', { mapping, mode }, csrf)))}>Validate preview</Button>
        </fieldset>
        {job.preview ? <div className="content-stack">
          <h4>3. Review validation</h4>
          <StatePanel kind={job.preview.valid ? 'info' : 'error'} title={job.preview.valid ? 'Ready to commit' : 'File has errors'}
            message={job.preview.valid ? `Planned: ${counts(job.preview.summary)}. No business records have been saved.` : `${job.preview.errors.length} errors. Nothing will be saved until every row is valid.`} />
          {job.preview.errors.length ? <>
            <a href={`/api/admin/imports/${job.id}/errors`} download>Download row errors</a>
            <div className="import-table" tabIndex={0} role="region" aria-label="Validation errors"><table><thead><tr><th>Row</th><th>Field</th><th>Reason</th></tr></thead>
              <tbody>{job.preview.errors.slice(0, 100).map((e, i) => <tr key={i}><td>{e.row}</td><td>{label(e.field)}</td><td>{e.message}</td></tr>)}</tbody></table></div>
            <p>Showing the first 100 errors. Correct the source file and upload it again, or fix the mapping and validate again.</p>
          </> : null}
          <Button type="button" disabled={busy || denied || !job.preview.valid} onClick={() => void run(async () => {
            setUncertain(true)
            try {
              const result = await api<Result>(`/api/admin/imports/${job.id}/commit`, 'POST', { preview_token: job.preview_token, idempotency_key: job.id }, csrf)
              setJob(previous => previous ? { ...previous, result } : null); setUncertain(false)
            } catch (caught) {
              if (caught instanceof ApiError && caught.status === 409) { setUncertain(false); invalidate() }
              throw caught
            }
          })}>{uncertain ? 'Retry same commit' : 'Commit entire file'}</Button>
          {uncertain ? <p role="status">Commit outcome is not confirmed. Retry the same commit safely; it cannot apply twice.</p> : null}
        </div> : null}
      </> : <StatePanel kind="success" title="Import committed" message={counts(job.result)} />}
      {job.result ? <Button type="button" disabled={busy} onClick={() => { setJob(null); setError(''); if (fileInput.current) fileInput.current.value = '' }}>Import another file</Button> : null}
    </> : null}
    <div className="content-stack"><h4>Recent imports</h4>
      <Button type="button" variant="secondary" disabled={busy || uncertain || denied} onClick={() => void run(async () => setHistory(await api<History[]>('/api/admin/imports')))}>Refresh history</Button>
      {history.length ? <ul>{history.map(item => <li key={item.id}><Button type="button" variant="secondary" disabled={busy || uncertain || denied}
        onClick={() => void run(async () => accept(await api<Job>(`/api/admin/imports/${item.id}`)))}>{item.filename} · {item.status}</Button></li>)}</ul> : <p>No recent imports loaded. Refresh to see up to 50 jobs.</p>}
    </div>
  </section>
}
