// The catalog-parity gate: every locale ships exactly the key set of the English
// source, and the three declarations of "which locales exist" (message files, the
// inlang project, UI_LOCALES) never drift apart. Translations are regenerated from
// English — a key missing here means a string silently falls back for that locale.
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'

import { UI_LOCALES } from './locale.ts'

const messagesDir = fileURLToPath(new URL('../../messages/', import.meta.url))
const settingsPath = fileURLToPath(new URL('../../project.inlang/settings.json', import.meta.url))

function keysOf(locale: string): string[] {
  const parsed = JSON.parse(readFileSync(`${messagesDir}${locale}.json`, 'utf8')) as Record<
    string,
    unknown
  >
  return Object.keys(parsed).filter((k) => !k.startsWith('$'))
}

test('a message file exists for every supported locale, and nothing else', () => {
  const files = readdirSync(messagesDir).filter((f) => f.endsWith('.json'))
  assert.deepEqual(files.sort(), [...UI_LOCALES].map((l) => `${l}.json`).sort())
})

test('the inlang project declares exactly the supported locales', () => {
  const settings = JSON.parse(readFileSync(settingsPath, 'utf8')) as {
    baseLocale: string
    locales: string[]
  }
  assert.equal(settings.baseLocale, 'en')
  assert.deepEqual(settings.locales.sort(), [...UI_LOCALES].sort())
})

test('every locale carries exactly the key set of the English source', () => {
  const base = keysOf('en').sort()
  assert.ok(base.length > 0)
  for (const locale of UI_LOCALES) {
    if (locale === 'en') continue
    assert.deepEqual(keysOf(locale).sort(), base, `catalog ${locale}.json diverges from en.json`)
  }
})

test('no locale leaves a key untranslated as an empty string', () => {
  for (const locale of UI_LOCALES) {
    const parsed = JSON.parse(readFileSync(`${messagesDir}${locale}.json`, 'utf8')) as Record<
      string,
      unknown
    >
    for (const [key, value] of Object.entries(parsed)) {
      if (key.startsWith('$')) continue
      if (typeof value === 'string') assert.ok(value.trim().length > 0, `${locale}.json ${key}`)
    }
  }
})
