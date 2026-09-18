import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
} from 'react'

export type StatusTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

type AppFrameProps = {
  eyebrow?: string
  title: string
  meta?: ReactNode
  children: ReactNode
}

export function AppFrame({ eyebrow, title, meta, children }: AppFrameProps) {
  return (
    <main className="app-shell">
      <div className="app-frame">
        <header className="app-header">
          <div>
            {eyebrow ? <p className="eyebrow">{eyebrow}</p> : null}
            <h1 id="page-title">{title}</h1>
          </div>
          {meta ? <div className="app-header__meta">{meta}</div> : null}
        </header>
        <div className="app-panel">{children}</div>
      </div>
    </main>
  )
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'danger'
  busy?: boolean
}

export function Button({
  variant = 'primary',
  busy = false,
  disabled,
  className,
  children,
  ...props
}: ButtonProps) {
  const classes = ['button', 'button--' + variant, className].filter(Boolean).join(' ')

  return (
    <button
      {...props}
      className={classes}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {children}
    </button>
  )
}

type TextFieldProps = Omit<InputHTMLAttributes<HTMLInputElement>, 'id' | 'name'> & {
  id: string
  name: string
  label: string
  help?: string
  error?: string
}

export function TextField({ id, name, label, help, error, ...props }: TextFieldProps) {
  const helpId = help ? id + '-help' : undefined
  const errorId = error ? id + '-error' : undefined
  const describedBy = [helpId, errorId].filter(Boolean).join(' ') || undefined

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        {...props}
        id={id}
        name={name}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
      />
      {help ? <p id={helpId} className="field__help">{help}</p> : null}
      {error ? <p id={errorId} className="field__error">{error}</p> : null}
    </div>
  )
}

type SelectFieldProps = Omit<SelectHTMLAttributes<HTMLSelectElement>, 'id' | 'name'> & {
  id: string
  name: string
  label: string
  help?: string
  children: ReactNode
}

export function SelectField({ id, name, label, help, children, ...props }: SelectFieldProps) {
  const helpId = help ? id + '-help' : undefined

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <select {...props} id={id} name={name} aria-describedby={helpId}>
        {children}
      </select>
      {help ? <p id={helpId} className="field__help">{help}</p> : null}
    </div>
  )
}

type StatusBadgeProps = {
  tone?: StatusTone
  children: ReactNode
}

export function StatusBadge({ tone = 'neutral', children }: StatusBadgeProps) {
  return <span className={'status-badge status-badge--' + tone}>{children}</span>
}

type StateKind = 'loading' | 'empty' | 'error' | 'success' | 'info'

type StatePanelProps = {
  kind: StateKind
  title: string
  message: string
  action?: ReactNode
}

export function StatePanel({ kind, title, message, action }: StatePanelProps) {
  const role =
    kind === 'error'
      ? 'alert'
      : kind === 'loading' || kind === 'success' || kind === 'info'
        ? 'status'
        : undefined

  return (
    <div
      className={'state-panel state-panel--' + kind}
      role={role}
      aria-live={role === 'status' ? 'polite' : undefined}
      aria-busy={kind === 'loading' ? true : undefined}
    >
      <div className="state-panel__copy">
        <strong>{title}</strong>
        <p>{message}</p>
      </div>
      {action ? <div className="state-panel__action">{action}</div> : null}
    </div>
  )
}
