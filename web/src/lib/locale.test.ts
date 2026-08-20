// The UI-language resolution seam: stored per-device choice → browser languages →
// English. Pure logic — the callers own localStorage and navigator.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { LOCALE_ENDONYM, UI_LOCALES, isUiLocale, resolveUiLocale } from './locale.ts'

test('a stored choice beats the browser languages', () => {
  assert.equal(resolveUiLocale('ru', ['de-DE', 'de']), 'ru')
})

test('a stored value that is no longer a supported locale is ignored', () => {
  assert.equal(resolveUiLocale('tlh', ['es-MX']), 'es')
  assert.equal(resolveUiLocale('', ['es']), 'es')
})

test('browser languages are matched in preference order', () => {
  assert.equal(resolveUiLocale(null, ['fr-FR', 'de-AT', 'en-GB']), 'de')
})

test('exact tags match case-insensitively', () => {
  assert.equal(resolveUiLocale(null, ['zh-cn']), 'zh-CN')
  assert.equal(resolveUiLocale(null, ['pt-br']), 'pt-BR')
})

test('regional variants fold onto the supported locale', () => {
  assert.equal(resolveUiLocale(null, ['ru-RU']), 'ru')
  assert.equal(resolveUiLocale(null, ['es-419']), 'es')
  assert.equal(resolveUiLocale(null, ['de-CH']), 'de')
  assert.equal(resolveUiLocale(null, ['hi-IN']), 'hi')
})

test('bare and non-Brazilian Portuguese fold onto pt-BR', () => {
  assert.equal(resolveUiLocale(null, ['pt']), 'pt-BR')
  assert.equal(resolveUiLocale(null, ['pt-PT']), 'pt-BR')
})

test('Chinese variants fold onto zh-CN', () => {
  assert.equal(resolveUiLocale(null, ['zh']), 'zh-CN')
  assert.equal(resolveUiLocale(null, ['zh-Hans']), 'zh-CN')
  assert.equal(resolveUiLocale(null, ['zh-TW']), 'zh-CN')
})

test('unsupported browser languages fall back to English', () => {
  assert.equal(resolveUiLocale(null, ['fr-FR', 'ja']), 'en')
  assert.equal(resolveUiLocale(null, []), 'en')
})

test('every supported locale has an endonym for the selector', () => {
  for (const locale of UI_LOCALES) assert.ok(LOCALE_ENDONYM[locale].length > 0)
})

test('isUiLocale guards arbitrary strings', () => {
  assert.equal(isUiLocale('zh-CN'), true)
  assert.equal(isUiLocale('zh'), false)
  assert.equal(isUiLocale(null), false)
})
