import assert from 'node:assert/strict'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve, sep } from 'node:path'
import test from 'node:test'
import { databaseEnvironment } from './database-env.mjs'

test('native configuration follows Compose defaults, .env, shell precedence and URL overrides', () => {
  const original = process.cwd()
  const temporary = mkdtempSync(join(tmpdir(), 'qt002-config-'))
  const env = { ...process.env }
  for (const key of Object.keys(env)) {
    if (key.startsWith('POSTGRES_') || key.startsWith('COMPOSE_') || key === 'DATABASE_URL') delete env[key]
  }
  try {
    writeFileSync(join(temporary, 'docker-compose.yml'), readFileSync(join(original, 'docker-compose.yml')))
    process.chdir(temporary)
    let url = new URL(databaseEnvironment(env).DATABASE_URL)
    assert.equal(url.hostname, '127.0.0.1')
    assert.equal(url.port, '5432')
    assert.equal(url.username, 'quotepilot')
    assert.equal(url.pathname, '/quotepilot_dev')
    assert.equal(new URL(databaseEnvironment({ ...env, DATABASE_URL: '' }).DATABASE_URL).pathname, '/quotepilot_dev')

    writeFileSync('.env', "POSTGRES_USER=custom\nPOSTGRES_PASSWORD='p@ss%word'\nPOSTGRES_DB=custom\nPOSTGRES_PORT=5544\n")
    url = new URL(databaseEnvironment(env).DATABASE_URL)
    assert.equal(decodeURIComponent(url.password), 'p@ss%word')
    assert.equal(url.port, '5544')
    assert.equal(url.username, 'custom')
    assert.equal(url.pathname, '/custom')
    assert.equal(new URL(databaseEnvironment({ ...env, POSTGRES_PORT: '5545' }).DATABASE_URL).port, '5545')

    const override = 'postgresql://deployment:secret@remote:5433/production'
    writeFileSync('.env', `DATABASE_URL=${override}\n`)
    assert.equal(databaseEnvironment(env).DATABASE_URL, override)
    assert.equal(databaseEnvironment({ ...env, DATABASE_URL: 'invalid' }).DATABASE_URL, 'invalid')
    assert.throws(() => databaseEnvironment({ ...env, COMPOSE_FILE: 'missing.yml' }), /Cannot resolve/)
  } finally {
    process.chdir(original)
    assert.ok(resolve(temporary).startsWith(resolve(tmpdir()) + sep))
    rmSync(temporary, { recursive: true })
  }
})
