import { spawn } from 'node:child_process'
import { databaseEnvironment } from './database-env.mjs'

try {
  const child = spawn(process.platform === 'win32' ? 'uv.exe' : 'uv',
    ['--directory', 'apps/server', 'run', ...process.argv.slice(2)],
    { stdio: 'inherit', env: databaseEnvironment() })
  child.once('error', () => { console.error('Unable to start server command.'); process.exitCode = 1 })
  child.once('close', (code) => { process.exitCode = code ?? 1 })
} catch (error) {
  console.error(error.message)
  process.exitCode = 1
}
