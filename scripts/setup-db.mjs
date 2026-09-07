import { spawn } from 'node:child_process'
import process from 'node:process'

const dockerCommand = process.platform === 'win32' ? 'docker.exe' : 'docker'

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: 'inherit', env: process.env })
    child.once('error', reject)
    child.once('close', (code) => resolve(code ?? 1))
  })
}

async function runDocker(args, failureMessage) {
  try {
    const exitCode = await run(dockerCommand, args)
    if (exitCode !== 0) {
      console.error(failureMessage)
      process.exit(exitCode)
    }
  } catch (error) {
    console.error(`${failureMessage} ${error instanceof Error ? error.message : String(error)}`)
    process.exit(1)
  }
}

await runDocker(
  ['compose', 'up', '-d', '--wait', 'db'],
  'Unable to create or start the QuotePilot PostgreSQL service with Docker Compose.',
)

const validationSql = [
  'BEGIN;',
  'CREATE TEMP TABLE quotepilot_db_check (value integer NOT NULL);',
  'INSERT INTO quotepilot_db_check VALUES (1);',
  'SELECT current_database() AS database, current_user AS user_name, value FROM quotepilot_db_check;',
  'ROLLBACK;',
].join(' ')

const validationCommand = [
  'PGPASSWORD="$POSTGRES_PASSWORD"',
  'PGCONNECT_TIMEOUT=5',
  'psql',
  '--no-psqlrc',
  '-h',
  '127.0.0.1',
  '-U',
  '"$POSTGRES_USER"',
  '-d',
  '"$POSTGRES_DB"',
  '-v',
  'ON_ERROR_STOP=1',
  '-c',
  `'${validationSql}'`,
].join(' ')

try {
  const validationExitCode = await run(dockerCommand, [
    'compose',
    'exec',
    '-T',
    'db',
    'sh',
    '-lc',
    validationCommand,
  ])

  if (validationExitCode !== 0) {
    console.error('PostgreSQL is running, but QuotePilot could not authenticate to the configured development database or complete the SQL read/write check.')
    console.error('An existing PostgreSQL volume keeps the database/user/password from its initial creation; changing POSTGRES_* values does not rewrite that stored database configuration.')
    console.error('If this is disposable local data and you intentionally want to reset it, run `docker compose down -v` and then rerun `npm run setup:db`.')
    process.exit(validationExitCode)
  }
} catch (error) {
  console.error(`Unable to validate the QuotePilot PostgreSQL configuration: ${error instanceof Error ? error.message : String(error)}`)
  process.exit(1)
}

console.error('QuotePilot PostgreSQL is healthy and the configured credentials passed an authenticated SQL read/write check.')
