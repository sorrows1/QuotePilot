import { execFileSync } from 'node:child_process'

export function databaseEnvironment(env = process.env) {
  if (env.DATABASE_URL) return env
  let config
  try {
    // Compose is the source of development defaults, .env expansion and shell overrides.
    config = JSON.parse(execFileSync(process.platform === 'win32' ? 'docker.exe' : 'docker',
      ['compose', 'config', '--format', 'json'], { env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }))
  } catch {
    throw new Error('Cannot resolve development database configuration with Docker Compose.')
  }
  const override = config.services.api.environment.DATABASE_URL
  if (override) return { ...env, DATABASE_URL: override }
  const db = config.services.db
  const port = db.ports.find((entry) => entry.target === 5432)?.published
  const { POSTGRES_USER: user, POSTGRES_PASSWORD: password, POSTGRES_DB: name } = db.environment
  if (!port || !user || !password || !name) throw new Error('Incomplete Compose database configuration.')
  const url = `postgresql://${encodeURIComponent(user)}:${encodeURIComponent(password)}@127.0.0.1:${port}/${encodeURIComponent(name)}`
  return { ...env, DATABASE_URL: url }
}
