import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import process from 'node:process'

const missing = [
  ['apps/web/node_modules', 'frontend dependencies'],
  ['apps/server/.venv', 'server virtual environment'],
].filter(([path]) => !existsSync(path))

if (missing.length > 0) {
  console.error(`Local dependencies are not set up: ${missing.map(([, label]) => label).join(', ')}.`)
  console.error('Run `npm run setup` from the repository root, then rerun `npm run dev`.')
  process.exit(1)
}

const uvCommand = process.platform === 'win32' ? 'uv.exe' : 'uv'
const dockerCommand = process.platform === 'win32' ? 'docker.exe' : 'docker'

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: 'inherit', env: process.env })
    child.once('error', reject)
    child.once('close', (code) => resolve(code ?? 1))
  })
}

try {
  const databaseExitCode = await run(dockerCommand, ['compose', 'up', '-d', '--wait', 'db'])
  if (databaseExitCode !== 0) process.exit(databaseExitCode)
} catch (error) {
  console.error(`Unable to start PostgreSQL with Docker Compose: ${error instanceof Error ? error.message : String(error)}`)
  process.exit(1)
}

const children = []
let shuttingDown = false

function start(name, command, args) {
  const child = spawn(command, args, { stdio: 'inherit', env: process.env })
  children.push(child)
  child.once('error', (error) => {
    if (shuttingDown) return
    console.error(`${name} failed to start: ${error.message}`)
    void shutdown(1)
  })
  child.once('close', (code, signal) => {
    if (shuttingDown) return
    const detail = signal ? `signal ${signal}` : `exit code ${code ?? 1}`
    console.error(`${name} stopped unexpectedly (${detail}).`)
    void shutdown(code && code !== 0 ? code : 1)
  })
  return child
}

function stopChild(child) {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) return Promise.resolve()

  return new Promise((resolve) => {
    let settled = false
    const finish = () => {
      if (settled) return
      settled = true
      resolve()
    }

    child.once('close', finish)

    if (process.platform === 'win32') {
      const killer = spawn('taskkill', ['/pid', String(child.pid), '/t', '/f'], { stdio: 'ignore' })
      killer.once('error', finish)
      killer.once('close', finish)
    } else {
      child.kill('SIGTERM')
    }

    const timeout = setTimeout(() => {
      if (process.platform !== 'win32' && child.exitCode === null && child.signalCode === null) {
        child.kill('SIGKILL')
      }
      finish()
    }, 3000)
    timeout.unref()
  })
}

async function shutdown(exitCode) {
  if (shuttingDown) return
  shuttingDown = true
  await Promise.all(children.map(stopChild))
  console.error('QuotePilot web/server stopped. PostgreSQL remains running; use `docker compose stop db` to stop it.')
  process.exit(exitCode)
}

process.once('SIGINT', () => void shutdown(0))
process.once('SIGTERM', () => void shutdown(0))

start('FastAPI server', uvCommand, [
  '--directory',
  'apps/server',
  'run',
  'uvicorn',
  'quotepilot_api.main:app',
  '--reload',
  '--host',
  '0.0.0.0',
  '--port',
  '8000',
])
start('Vite web app', process.execPath, [
  'apps/web/node_modules/vite/bin/vite.js',
  '--host',
  '0.0.0.0',
])

console.error('QuotePilot development environment is running:')
console.error('  Web: http://localhost:5173')
console.error('  API health: http://localhost:8000/health')
console.error('Press Ctrl+C to stop the web and server processes.')
