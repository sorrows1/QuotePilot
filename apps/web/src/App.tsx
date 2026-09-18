import { useEffect, useState, type FormEvent } from 'react'

type Me = { user_id: string; login: string; role: string; must_change_password: boolean; csrf_token: string }
type User = { id: string; login: string; role: string; disabled: boolean; version: number }
class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}
async function api<T>(path: string, method = 'GET', body?: unknown, csrf = ''): Promise<T> {
  const response = await fetch(path, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-QuotePilot-Request': '1', 'X-CSRF-Token': csrf },
    ...(method !== 'GET' ? { body: JSON.stringify(body ?? {}) } : {}),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new ApiError(response.status, typeof error.message === 'string' ? error.message : 'Service unavailable. Try again.')
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}
function field(form: HTMLFormElement, key: string) { return String(new FormData(form).get(key) ?? '') }
export function App() {
  const [me, setMe] = useState<Me | null>(null)
  const [restoring, setRestoring] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [users, setUsers] = useState<User[]>([])
  const [notice, setNotice] = useState('')
  useEffect(() => {
    let active = true
    const restore = async () => {
      try { const user = await api<Me>('/api/me'); if (active) setMe(user) }
      catch (e) { if (active && !(e instanceof ApiError && e.status === 401)) setError('Unable to restore your session. Reload to retry.') }
      finally { if (active) setRestoring(false) }
    }
    void restore()
    return () => { active = false }
  }, [])
  useEffect(() => {
    if (!me) return
    let active = true
    const check = async () => {
      try { const user = await api<Me>('/api/me'); if (active) setMe(user) }
      catch (e) {
        if (active && e instanceof ApiError && e.status === 401) {
          setMe(null); setUsers([]); setError('Your session ended. Sign in again.')
        }
      }
    }
    const timer = setInterval(() => void check(), 30000)
    window.addEventListener('focus', check)
    return () => { active = false; clearInterval(timer); window.removeEventListener('focus', check) }
  }, [me?.user_id])
  async function run(action: () => Promise<void>) {
    setBusy(true); setError(''); setNotice('')
    try { await action() }
    catch (e) {
      setError(e instanceof Error ? e.message : 'Service unavailable. Try again.')
      if (e instanceof ApiError && e.status === 401 && me) { setMe(null); setUsers([]) }
    } finally { setBusy(false) }
  }
  function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    void run(async () => {
      const user = await api<Me>('/api/auth/login', 'POST', { organization: field(form, 'organization'), login: field(form, 'login'), password: field(form, 'password') })
      form.reset(); setMe(user)
    })
  }
  function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    void run(async () => {
      const user = await api<Me>('/api/auth/password', 'POST', { current_password: field(form, 'current'), new_password: field(form, 'new') }, me?.csrf_token)
      form.reset(); setMe(user); setNotice('Password changed. Your previous sessions have ended.')
    })
  }
  function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    void run(async () => {
      await api<User>('/api/admin/users', 'POST', { login: field(form, 'login'), password: field(form, 'password'), role: field(form, 'role') }, me?.csrf_token)
      form.reset(); setUsers(await api<User[]>('/api/admin/users'))
      setNotice('User created. Deliver the initial password through your approved secure channel. They must change it at first login.')
    })
  }
  return <main className="app-shell"><section className="hero" aria-labelledby="page-title">
    <p className="eyebrow">Quotation preparation workspace</p><h1 id="page-title">QuotePilot</h1>
    {error ? <p role="alert" className="error">{error}</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {restoring ? <p role="status">Restoring session…</p> : !me ? <>
      <h2>Sign in</h2><p>Use the organization code and credentials supplied by your administrator.</p>
      <form onSubmit={login}><fieldset disabled={busy}>
        <label>Organization code<input name="organization" required minLength={3} maxLength={64} autoComplete="organization" /></label>
        <label>Login<input name="login" required maxLength={128} autoComplete="username" /></label>
        <label>Password<input name="password" type="password" required maxLength={128} autoComplete="current-password" /></label>
        <button type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
      </fieldset></form>
    </> : <>
      <p>Signed in as <strong>{me.login}</strong> · {me.role.replaceAll('_', ' ')}</p>
      <button disabled={busy} onClick={() => void run(async () => { await api('/api/auth/logout', 'POST', {}, me.csrf_token); setMe(null); setUsers([]) })}>Log out</button>
      {me.must_change_password ? <>
        <h2>Change your initial password</h2><p>Choose a new password of 15–128 characters to enter your workspace.</p>
        <form onSubmit={changePassword}><fieldset disabled={busy}>
          <label>Current password<input name="current" type="password" autoComplete="current-password" required maxLength={128} /></label>
          <label>New password<input name="new" type="password" autoComplete="new-password" required minLength={15} maxLength={128} /></label>
          <button>Change password</button>
        </fieldset></form>
      </> : <>
        <h2>Workspace</h2><p className="lede">You are signed in. Quotation workflows will be added in later implementation tasks.</p>
        <button disabled={busy} onClick={() => void run(async () => { setMe(await api<Me>('/api/auth/refresh', 'POST', {}, me.csrf_token)); setNotice('Session renewed for up to 30 minutes.') })}>Keep working</button>
        {me.role === 'system_admin' ? <section aria-labelledby="users-title">
          <h2 id="users-title">Manage users</h2>
          <form onSubmit={createUser}><fieldset disabled={busy}><legend>Create user</legend>
            <label>New user login<input name="login" required maxLength={128} autoComplete="off" /></label>
            <label>Initial password<input name="password" type="password" required minLength={15} maxLength={128} autoComplete="new-password" /></label>
            <label>Role<select name="role"><option value="sales_admin">Sales administrator</option><option value="sales_manager">Sales manager</option><option value="system_admin">System administrator</option></select></label>
            <button>Create user</button>
          </fieldset></form>
          <button disabled={busy} onClick={() => void run(async () => setUsers(await api<User[]>('/api/admin/users')))}>Refresh users</button>
          <p>Showing up to 200 users.</p>
          <ul className="users">{users.map(user => <li key={user.id}>
            <strong>{user.login}</strong> · {user.disabled ? 'Disabled' : 'Active'}
            <div className="actions">{[user.disabled ? 'Enable' : 'Disable', 'Revoke sessions'].map(action => <button key={action} disabled={busy} onClick={() => void run(async () => {
              await api(`/api/admin/users/${user.id}`, 'PATCH', { version: user.version, ...(action === 'Revoke sessions' ? { revoke_sessions: true } : { disabled: !user.disabled }) }, me.csrf_token)
              setUsers(await api<User[]>('/api/admin/users')); setNotice(`${action} completed for ${user.login}.`)
            })}>{action}</button>)}</div>
          </li>)}</ul>
        </section> : null}
      </>}
    </>}
  </section></main>
}
