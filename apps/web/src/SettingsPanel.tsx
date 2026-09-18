import { useEffect, useRef, useState } from 'react'
import { api, ApiError } from './api'

type Value = string | number | boolean | null
interface Settings {
  [key: string]: Value | Record<string, string>
  edit_version: number
  setup_complete: boolean
  field_errors: Record<string, string>
  current_logo_asset_id: string | null
}
const groups = [
  ['company_name', 'company_address'],
  ['tax_enabled', 'tax_rate', 'freight_taxable', 'inventory_max_age_minutes', 'default_quote_validity_days', 'quote_number_prefix'],
  ['discount_approval_enabled', 'discount_approval_threshold', 'quote_margin_control_enabled', 'minimum_margin_threshold', 'line_margin_control_enabled', 'minimum_line_margin_threshold', 'quote_value_approval_enabled', 'quote_value_approval_threshold'],
]
const titles = ['Company', 'Quote defaults', 'Approval controls', 'Review']
const labels: Record<string, string> = {
  company_name: 'Company name', company_address: 'Company address (optional)', tax_enabled: 'Tax enabled', tax_rate: 'Tax rate', freight_taxable: 'Freight taxable', inventory_max_age_minutes: 'Inventory freshness limit (minutes)', default_quote_validity_days: 'Quote validity (calendar days)', quote_number_prefix: 'Quote number prefix', discount_approval_enabled: 'Discount approval', discount_approval_threshold: 'Discount threshold', quote_margin_control_enabled: 'Quote margin control', minimum_margin_threshold: 'Minimum quote margin', line_margin_control_enabled: 'Line margin control', minimum_line_margin_threshold: 'Minimum line margin', quote_value_approval_enabled: 'Quote value approval', quote_value_approval_threshold: 'Quote value threshold (SGD)',
}
const dependencies: Record<string, string> = { tax_rate: 'tax_enabled', freight_taxable: 'tax_enabled', discount_approval_threshold: 'discount_approval_enabled', minimum_margin_threshold: 'quote_margin_control_enabled', minimum_line_margin_threshold: 'line_margin_control_enabled', quote_value_approval_threshold: 'quote_value_approval_enabled' }
const isBoolean = (key: string) => key.endsWith('_enabled') || key === 'freight_taxable'
const isInteger = (key: string) => key === 'inventory_max_age_minutes' || key === 'default_quote_validity_days'
function firstIncomplete(settings: Settings) { const index = groups.findIndex(group => group.some(key => key in settings.field_errors)); return index < 0 ? 3 : index }

export function SettingsPanel({ csrf, onComplete }: { csrf: string; onComplete: () => void }) {
  const [saved, setSaved] = useState<Settings | null>(null)
  const [values, setValues] = useState<Record<string, Value>>({})
  const [step, setStep] = useState(0)
  const [view, setView] = useState<'form' | 'later' | 'success' | 'imports'>('form')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [failed, setFailed] = useState(false)
  const summary = useRef<HTMLDivElement>(null)
  function accept(settings: Settings) {
    setSaved(settings)
    setValues(Object.fromEntries(groups.flat().map(key => [key, settings[key] as Value])))
  }
  useEffect(() => {
    let active = true
    api<Settings>('/api/admin/settings').then(settings => { if (active) { accept(settings); setStep(firstIncomplete(settings)) } }).catch(() => { if (active) { setMessage('Unable to load settings. Retry.'); setFailed(true) } })
    return () => { active = false }
  }, [])
  async function recover(error: unknown) {
    setFailed(true)
    if (error instanceof ApiError && error.code === 'SETTINGS_VERSION_CONFLICT') {
      setMessage('Settings changed elsewhere. Reloading current values; review them before saving again.')
      try { const latest = await api<Settings>('/api/admin/settings'); accept(latest); setErrors({}); setMessage('Settings changed elsewhere. Current values loaded. Review and reapply your changes before saving.') }
      catch { setSaved(null); setMessage('Unable to reload current settings. Retry before saving.') }
    } else {
      setMessage(error instanceof Error ? error.message : 'Unable to save. Retry.')
      setErrors(error instanceof ApiError ? error.fields : {})
    }
    requestAnimationFrame(() => summary.current?.focus())
  }
  function edit(key: string, value: Value) {
    setValues(previous => {
      const next = { ...previous, [key]: value }
      for (const [child, parent] of Object.entries(dependencies)) if (parent === key && value !== true) next[child] = null
      return next
    })
    setErrors(previous => { const next = { ...previous }; delete next[key]; return next })
    setMessage('')
  }
  function payload() {
    const changed: Record<string, Value> = {}
    for (const key of groups.flat()) {
      let value = values[key]
      if (value === '') value = null
      if (isInteger(key) && typeof value === 'string' && /^\d+$/.test(value)) value = Number(value)
      if (value !== saved?.[key]) changed[key] = value
    }
    return changed
  }
  async function save(destination: 'stay' | 'next' | 'later' | 'complete') {
    if (!saved) return
    setBusy(true); setMessage(''); setErrors({}); setFailed(false)
    try {
      let result = await api<Settings>('/api/admin/settings', 'PATCH', { ...payload(), expected_edit_version: saved.edit_version }, csrf)
      accept(result)
      if (destination === 'complete') {
        result = await api<Settings>('/api/admin/settings/complete', 'POST', { expected_edit_version: result.edit_version }, csrf)
        accept(result); setView('success'); onComplete()
      } else if (destination === 'next') {
        const incomplete = Object.fromEntries(Object.entries(result.field_errors).filter(([key]) => groups[step].includes(key)))
        if (Object.keys(incomplete).length) { setErrors(incomplete); setFailed(true); setMessage('Complete this step before continuing.'); requestAnimationFrame(() => summary.current?.focus()) }
        else setStep(step + 1)
      } else if (destination === 'later') setView('later')
      else setMessage(result.setup_complete ? 'Settings saved.' : 'Draft saved. Setup is not complete.')
    } catch (error) { await recover(error) }
    finally { setBusy(false) }
  }
  async function logo(file?: File) {
    if (!saved) return
    setBusy(true); setMessage(''); setErrors({}); setFailed(false)
    try {
      // Save entered company fields before accepting the updated branding response.
      const draft = await api<Settings>('/api/admin/settings', 'PATCH', { ...payload(), expected_edit_version: saved.edit_version }, csrf)
      accept(draft)
      let data: Record<string, unknown> = { expected_edit_version: draft.edit_version }
      if (file) {
        if (!['image/png', 'image/jpeg'].includes(file.type) || file.size > 2097152) throw new Error('Choose a PNG or JPEG of at most 2 MiB.')
        const encoded = await new Promise<string>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file) })
        data = { ...data, media_type: file.type, data_base64: encoded }
      }
      accept(await api<Settings>('/api/admin/settings/logo', file ? 'PUT' : 'DELETE', data, csrf))
      setMessage(file ? 'Logo saved.' : 'Logo removed.')
    } catch (error) { await recover(error) }
    finally { setBusy(false) }
  }
  const dirty = saved && Object.keys(payload()).length > 0
  function navigate(index: number) {
    if (dirty && !window.confirm('Discard unsaved edits and change step? Choose Cancel to stay.')) return
    if (saved) accept(saved)
    setErrors({}); setMessage(''); setStep(index)
  }
  return <section aria-labelledby="settings-title" className="settings-panel">
    <h2 id="settings-title">{saved?.setup_complete ? 'Company settings' : 'Set up your company for quotations'}</h2>
    {message ? <div ref={summary} tabIndex={-1} role={failed ? 'alert' : 'status'}><p>{message}</p>{Object.entries(errors).map(([key, text]) => <p key={key}><a href={`#setting-${key}`}>{labels[key] ?? key}: {text}</a></p>)}</div> : null}
    {!saved ? <><p role="status">{failed ? 'Settings unavailable.' : 'Loading settings…'}</p>{failed ? <button onClick={() => { setFailed(false); api<Settings>('/api/admin/settings').then(result => { accept(result); setStep(firstIncomplete(result)); setMessage('') }).catch(recover) }}>Retry</button> : null}</> : view === 'later' ? <><p>Company setup is {saved.setup_complete ? 'complete' : 'incomplete'}.</p><button onClick={() => { setStep(firstIncomplete(saved)); setView('form') }}>{saved.setup_complete ? 'Open settings' : 'Resume setup'}</button></> : view === 'imports' ? <><h3>Imports</h3><p>Customers, products, pricebooks and inventory are the next step. Import tools are not available yet.</p><button onClick={() => setView('form')}>Back to Settings</button></> : view === 'success' ? <><h3>Company setup is saved.</h3><p>Next: import customers, products, pricebooks and inventory. Imports are a separate workflow.</p><a href="#imports" onClick={() => setView('imports')}>Go to Imports</a><button onClick={() => setView('later')}>Go to QuotePilot</button></> : <>
      <p>{saved.setup_complete ? 'Changes become effective when saved.' : 'Save your progress at any time. Complete all four steps before moving to imports.'}</p>
      <nav aria-label="Setup progress" className="actions">{titles.map((title, index) => <button disabled={busy} key={title} aria-current={step === index ? 'step' : undefined} onClick={() => navigate(index)}>{title} · {index === 3 ? 'Review' : Object.keys(errors).some(key => groups[index].includes(key)) ? 'Needs attention' : groups[index].some(key => key in saved.field_errors) ? groups[index].some(key => saved[key] !== null) ? 'In progress' : 'Not started' : 'Complete'}</button>)}</nav>
      <h3>{titles[step]}</h3>
      <fieldset disabled={busy}>
        {step === 1 ? <p>Currency: <strong>SGD</strong> (fixed)</p> : null}
        {step < 3 ? groups[step].map(key => {
          const inactive = dependencies[key] && values[dependencies[key]] !== true
          if (inactive) return null
          return <label key={key} htmlFor={`setting-${key}`}>{labels[key]}
            {isBoolean(key) ? <select id={`setting-${key}`} aria-label={labels[key]} aria-required={key !== 'company_address'} value={values[key] === null ? '' : String(values[key])} onChange={event => edit(key, event.target.value === '' ? null : event.target.value === 'true')} aria-invalid={!!errors[key]} aria-describedby={`help-${key}`}><option value="">Choose explicitly</option><option value="true">{key.includes('tax') ? 'Yes' : 'Enabled'}</option><option value="false">{key.includes('tax') ? 'No' : 'Disabled'}</option></select> : key === 'company_address' ? <textarea id={`setting-${key}`} aria-label={labels[key]} aria-required={key !== 'company_address'} value={String(values[key] ?? '')} onChange={event => edit(key, event.target.value)} maxLength={1000} aria-invalid={!!errors[key]} aria-describedby={`help-${key}`} /> : <input id={`setting-${key}`} aria-label={labels[key]} aria-required={key !== 'company_address'} value={String(values[key] ?? '')} onChange={event => edit(key, event.target.value)} inputMode={isInteger(key) ? 'numeric' : key.includes('threshold') || key === 'tax_rate' ? 'decimal' : 'text'} aria-invalid={!!errors[key]} aria-describedby={`help-${key}`} />}
            <small id={`help-${key}`}>{errors[key] ?? (key === 'tax_rate' || key.includes('threshold') || key.startsWith('minimum_') ? key === 'quote_value_approval_threshold' ? 'Exact SGD amount, at least 0, at most 2 decimal places. Approval only above the threshold; equality does not trigger.' : `Exact fraction from 0 to 1, at most 6 decimal places.${key === 'tax_rate' ? '' : key.startsWith('minimum_') ? ' Approval only below the threshold; equality does not trigger. Missing cost remains blocked.' : ' Approval only above the threshold; equality does not trigger.'}` : key === 'default_quote_validity_days' ? 'Choose 1–365 whole calendar days.' : key === 'inventory_max_age_minutes' ? 'Positive whole minutes, up to 2147483647. Equality at the limit is fresh; future or stale inventory cannot support availability.' : key === 'quote_number_prefix' ? `1–10 letters/digits. Format example: ${String(values[key] || 'PREFIX').toUpperCase()}-000001 (not a guaranteed next number).` : '')}</small>
          </label>
        }) : <>{groups.map((group, index) => <section key={index}><h4>{titles[index]}</h4><dl>{group.filter(key => !dependencies[key] || values[dependencies[key]] === true).map(key => <div key={key}><dt>{labels[key]}</dt><dd>{values[key] === null ? 'Not provided' : typeof values[key] === 'boolean' ? values[key] ? 'Enabled / Yes' : 'Disabled / No' : String(values[key])}</dd></div>)}</dl><button onClick={() => navigate(index)}>Edit {titles[index]}</button></section>)}<p>Logo: {saved.current_logo_asset_id ? 'Saved' : 'Not provided (optional)'}</p>{Object.keys(saved.field_errors).length ? <p>Complete the missing settings before finishing.</p> : null}</>}
        {step === 0 ? <div><label>Company logo (optional)<input type="file" accept="image/png,image/jpeg" onChange={event => { const file = event.target.files?.[0]; if (file) void logo(file); event.target.value = '' }} /></label><p>PNG/JPEG, at most 2 MiB, 16–2048 pixels per side.</p>{saved.current_logo_asset_id ? <><img width="96" alt="Current company logo" src={`/api/admin/settings/logo?v=${saved.edit_version}`} /><button onClick={() => void logo()}>Remove logo</button></> : null}</div> : null}
        {step === 2 ? <p>Line and quote margins are independent: one line can require approval even when blended quote margin passes.</p> : null}
        <div className="actions">{step > 0 ? <button onClick={() => navigate(step - 1)}>Back</button> : null}<button onClick={() => void save('stay')}>{saved.setup_complete ? 'Save settings' : 'Save draft'}</button>{step < 3 ? <button onClick={() => void save('next')}>Save &amp; continue</button> : !saved.setup_complete ? <button disabled={Object.keys(saved.field_errors).length > 0 || !!dirty} onClick={() => void save('complete')}>Finish setup</button> : null}<button onClick={() => void save('later')}>{saved.setup_complete ? 'Close settings' : 'Finish later'}</button></div>
        {busy ? <p role="status">Saving…</p> : null}
      </fieldset>
    </>}
  </section>
}
