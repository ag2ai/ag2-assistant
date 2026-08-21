import { test } from 'node:test'
import assert from 'node:assert/strict'
import { invalidFolderName } from './folderName.ts'
import { m } from '../paraglide/messages.js'
import { setLocale } from '../paraglide/runtime.js'

// The picker's "new folder" field takes ONE name, not a path — `make_dir` would happily
// build a tree from "a/b/c" (the Files tab relies on that), and a dot-prefixed name would
// be created and then hidden by list_dirs. These RULES mirror `invalid_dir_name` in
// src/assistant/workspace.py; tests/test_workspace.py asserts the same table server-side,
// so a rule changed on one side without the other shows up as a failure here or there.
// The wording no longer has to match — it comes from the catalog now, so every assertion
// below names the MESSAGE rather than an English string and can't drift from it.

test('accepts ordinary folder names', () => {
  for (const ok of ['reports', 'market roundups', 'Q3-2026', 'a', 'naïve', '日本語', 'x'.repeat(255)]) {
    assert.equal(invalidFolderName(ok), null, `expected ${ok} to be accepted`)
  }
})

test('rejects empty and whitespace-only names', () => {
  assert.equal(invalidFolderName(''), m.folder_name_required())
  assert.equal(invalidFolderName('   '), m.folder_name_required())
  assert.equal(invalidFolderName(undefined), m.folder_name_required())
})

test('rejects surrounding whitespace but allows it inside', () => {
  assert.equal(invalidFolderName(' lead'), m.folder_name_edge_space())
  assert.equal(invalidFolderName('trail '), m.folder_name_edge_space())
  assert.equal(invalidFolderName('two words'), null)
})

test('rejects path separators — a name is not a path', () => {
  assert.equal(invalidFolderName('a/b'), m.folder_name_slashes())
  assert.equal(invalidFolderName('a\\b'), m.folder_name_slashes())
  assert.equal(invalidFolderName('../escape'), m.folder_name_slashes())
})

test('rejects the relative-directory names', () => {
  assert.equal(invalidFolderName('.'), m.folder_name_invalid())
  assert.equal(invalidFolderName('..'), m.folder_name_invalid())
})

test('rejects dotfolders — they would be created then hidden', () => {
  assert.equal(
    invalidFolderName('.hidden'),
    m.folder_name_dot_hidden(),
  )
})

test('rejects control characters', () => {
  assert.equal(invalidFolderName('nul\x00byte'), m.folder_name_bad_chars())
  assert.equal(invalidFolderName('tab\tsep'), m.folder_name_bad_chars())
  assert.equal(invalidFolderName('del\x7f'), m.folder_name_bad_chars())
})

test('measures length in bytes, not characters', () => {
  assert.equal(invalidFolderName('x'.repeat(256)), m.folder_name_too_long())
  // 128 two-byte chars = 256 bytes: within the character count, over NAME_MAX.
  assert.equal(invalidFolderName('é'.repeat(128)), m.folder_name_too_long())
  assert.equal(invalidFolderName('é'.repeat(127)), null)
})

// The assertions above hold in English whether or not the wording comes from the
// catalog, so this is the one that pins the migration: switch the UI language and the
// reason has to follow it. A hardcoded English string fails here and nowhere else.
test('the reason follows the UI language', () => {
  try {
    setLocale('ru', { reload: false })
    const why = invalidFolderName('')
    assert.equal(why, m.folder_name_required())
    assert.notEqual(why, 'Enter a folder name')
    assert.equal(invalidFolderName('a/b'), m.folder_name_slashes())
    assert.equal(invalidFolderName('good name'), null)
  } finally {
    setLocale('en', { reload: false })
  }
})
