import { useEffect, useState, type FormEvent } from 'react'
import {
  AppFrame,
  Button,
  SelectField,
  StatePanel,
  StatusBadge,
  TextField,
} from './ui'

type Me = {
  user_id: string
  login: string
  role: string
  must_change_password: boolean
  csrf_token: string
}

type User = {
  id: string
  login: string
  role: string
  disabled: boolean
  version: number
}

class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

async function api<T>(path: string, method = 'GET', body?: unknown, csrf = ''): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: {
      'Content-Type': 'application/json',
      'X-QuotePilot-Request': '1',
      'X-CSRF-Token': csrf,
    },
    ...(method !== 'GET' ? { body: JSON.stringify(body ?? {}) } : {}),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new ApiError(
      response.status,
      typeof error.message === 'string' ? error.message : 'Service unavailable. Try again.',
    )
  }

  return response.status === 204 ? undefined as T : response.json() as Promise<T>
}

function field(form: HTMLFormElement, key: string) {
  return String(new FormData(form).get(key) ?? '')
}

function roleLabel(role: string) {
  return role.replaceAll('_', ' ')
}

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
      try {
        const user = await api<Me>('/api/me')
        if (active) setMe(user)
      } catch (caught) {
        if (active && !(caught instanceof ApiError && caught.status === 401)) {
          setError('Unable to restore your session. Reload to retry.')
        }
      } finally {
        if (active) setRestoring(false)
      }
    }

    void restore()
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (!me) return

    let active = true
    const check = async () => {
      try {
        const user = await api<Me>('/api/me')
        if (active) setMe(user)
      } catch (caught) {
        if (active && caught instanceof ApiError && caught.status === 401) {
          setMe(null)
          setUsers([])
          setError('Your session ended. Sign in again.')
        }
      }
    }

    const timer = setInterval(() => void check(), 30000)
    window.addEventListener('focus', check)

    return () => {
      active = false
      clearInterval(timer)
      window.removeEventListener('focus', check)
    }
  }, [me?.user_id])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError('')
    setNotice('')

    try {
      await action()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Service unavailable. Try again.')
      if (caught instanceof ApiError && caught.status === 401 && me) {
        setMe(null)
        setUsers([])
      }
    } finally {
      setBusy(false)
    }
  }

  function login(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget

    void run(async () => {
      const user = await api<Me>('/api/auth/login', 'POST', {
        organization: field(form, 'organization'),
        login: field(form, 'login'),
        password: field(form, 'password'),
      })
      form.reset()
      setMe(user)
    })
  }

  function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget

    void run(async () => {
      const user = await api<Me>(
        '/api/auth/password',
        'POST',
        {
          current_password: field(form, 'current'),
          new_password: field(form, 'new'),
        },
        me?.csrf_token,
      )
      form.reset()
      setMe(user)
      setNotice('Password changed. Your previous sessions have ended.')
    })
  }

  function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget

    void run(async () => {
      await api<User>(
        '/api/admin/users',
        'POST',
        {
          login: field(form, 'login'),
          password: field(form, 'password'),
          role: field(form, 'role'),
        },
        me?.csrf_token,
      )
      form.reset()
      setUsers(await api<User[]>('/api/admin/users'))
      setNotice(
        'User created. Deliver the initial password through your approved secure channel. They must change it at first login.',
      )
    })
  }

  const headerMeta = me ? (
    <div className="session-chip">
      <span>{me.login}</span>
      <StatusBadge tone="info">{roleLabel(me.role)}</StatusBadge>
    </div>
  ) : (
    <StatusBadge tone="neutral">Secure workspace</StatusBadge>
  )

  return (
    <AppFrame
      eyebrow="Quotation preparation workspace"
      title="QuotePilot"
      meta={headerMeta}
    >
      <div className="content-stack">
        {error ? (
          <StatePanel kind="error" title="Action needed" message={error} />
        ) : null}
        {notice ? (
          <StatePanel kind="success" title="Updated" message={notice} />
        ) : null}

        {restoring ? (
          <StatePanel
            kind="loading"
            title="Restoring session"
            message="Checking your secure session before showing workspace controls."
          />
        ) : !me ? (
          <section className="content-section" aria-labelledby="sign-in-title">
            <div className="section-heading">
              <h2 id="sign-in-title">Sign in</h2>
              <p>Use the organization code and credentials supplied by your administrator.</p>
            </div>
            <form onSubmit={login}>
              <fieldset disabled={busy}>
                <TextField
                  id="organization"
                  name="organization"
                  label="Organization code"
                  required
                  minLength={3}
                  maxLength={64}
                  autoComplete="organization"
                />
                <TextField
                  id="login"
                  name="login"
                  label="Login"
                  required
                  maxLength={128}
                  autoComplete="username"
                />
                <TextField
                  id="password"
                  name="password"
                  label="Password"
                  type="password"
                  required
                  maxLength={128}
                  autoComplete="current-password"
                />
                <div className="form-actions">
                  <Button type="submit" busy={busy}>
                    {busy ? 'Signing in…' : 'Sign in'}
                  </Button>
                </div>
              </fieldset>
            </form>
          </section>
        ) : (
          <>
            <section className="session-summary" aria-label="Current session">
              <div>
                <p className="section-kicker">Current session</p>
                <p className="session-summary__identity">
                  Signed in as <strong>{me.login}</strong>
                </p>
              </div>
              <Button
                type="button"
                variant="secondary"
                disabled={busy}
                onClick={() => void run(async () => {
                  await api('/api/auth/logout', 'POST', {}, me.csrf_token)
                  setMe(null)
                  setUsers([])
                })}
              >
                Log out
              </Button>
            </section>

            {me.must_change_password ? (
              <section className="content-section" aria-labelledby="password-title">
                <div className="section-heading">
                  <h2 id="password-title">Change your initial password</h2>
                  <p>Choose a new password of 15–128 characters to enter your workspace.</p>
                </div>
                <form onSubmit={changePassword}>
                  <fieldset disabled={busy}>
                    <TextField
                      id="current-password"
                      name="current"
                      label="Current password"
                      type="password"
                      autoComplete="current-password"
                      required
                      maxLength={128}
                    />
                    <TextField
                      id="new-password"
                      name="new"
                      label="New password"
                      type="password"
                      autoComplete="new-password"
                      required
                      minLength={15}
                      maxLength={128}
                    />
                    <div className="form-actions">
                      <Button type="submit" busy={busy}>
                        Change password
                      </Button>
                    </div>
                  </fieldset>
                </form>
              </section>
            ) : (
              <>
                <section className="content-section" aria-labelledby="workspace-title">
                  <div className="section-heading">
                    <h2 id="workspace-title">Workspace</h2>
                    <p>
                      You are signed in. Quotation workflows will be added in later
                      implementation tasks.
                    </p>
                  </div>
                  <div className="form-actions">
                    <Button
                      type="button"
                      variant="secondary"
                      disabled={busy}
                      onClick={() => void run(async () => {
                        setMe(
                          await api<Me>(
                            '/api/auth/refresh',
                            'POST',
                            {},
                            me.csrf_token,
                          ),
                        )
                        setNotice('Session renewed for up to 30 minutes.')
                      })}
                    >
                      Keep working
                    </Button>
                  </div>
                </section>

                {me.role === 'system_admin' ? (
                  <section className="content-section" aria-labelledby="users-title">
                    <div className="section-heading">
                      <h2 id="users-title">Manage users</h2>
                      <p>Create users and manage access without leaving the workspace shell.</p>
                    </div>

                    <form onSubmit={createUser}>
                      <fieldset disabled={busy}>
                        <legend>Create user</legend>
                        <TextField
                          id="new-user-login"
                          name="login"
                          label="New user login"
                          required
                          maxLength={128}
                          autoComplete="off"
                        />
                        <TextField
                          id="initial-password"
                          name="password"
                          label="Initial password"
                          type="password"
                          required
                          minLength={15}
                          maxLength={128}
                          autoComplete="new-password"
                        />
                        <SelectField id="new-user-role" name="role" label="Role">
                          <option value="sales_admin">Sales administrator</option>
                          <option value="sales_manager">Sales manager</option>
                          <option value="system_admin">System administrator</option>
                        </SelectField>
                        <div className="form-actions">
                          <Button type="submit" busy={busy}>Create user</Button>
                        </div>
                      </fieldset>
                    </form>

                    <div className="list-toolbar">
                      <div>
                        <p className="section-kicker">Directory</p>
                        <p>Showing up to 200 users.</p>
                      </div>
                      <Button
                        type="button"
                        variant="secondary"
                        disabled={busy}
                        onClick={() => void run(async () => {
                          setUsers(await api<User[]>('/api/admin/users'))
                        })}
                      >
                        Refresh users
                      </Button>
                    </div>

                    <ul className="users">
                      {users.map((user) => (
                        <li key={user.id}>
                          <div className="user-summary">
                            <div>
                              <strong>{user.login}</strong>
                              <p>{roleLabel(user.role)}</p>
                            </div>
                            <StatusBadge tone={user.disabled ? 'warning' : 'success'}>
                              {user.disabled ? 'Disabled' : 'Active'}
                            </StatusBadge>
                          </div>
                          <div className="actions">
                            {[user.disabled ? 'Enable' : 'Disable', 'Revoke sessions'].map(
                              (action) => (
                                <Button
                                  key={action}
                                  type="button"
                                  variant={action === 'Disable' ? 'danger' : 'secondary'}
                                  disabled={busy}
                                  onClick={() => void run(async () => {
                                    await api(
                                      '/api/admin/users/' + user.id,
                                      'PATCH',
                                      {
                                        version: user.version,
                                        ...(action === 'Revoke sessions'
                                          ? { revoke_sessions: true }
                                          : { disabled: !user.disabled }),
                                      },
                                      me.csrf_token,
                                    )
                                    setUsers(await api<User[]>('/api/admin/users'))
                                    setNotice(action + ' completed for ' + user.login + '.')
                                  })}
                                >
                                  {action}
                                </Button>
                              ),
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </section>
                ) : null}
              </>
            )}
          </>
        )}
      </div>
    </AppFrame>
  )
}
