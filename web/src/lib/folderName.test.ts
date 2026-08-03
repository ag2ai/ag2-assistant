import { test } from 'node:test'
import assert from 'node:assert/strict'
import { invalidFolderName } from './folderName.js'

// The picker's "new folder" field takes ONE name, not a path — `make_dir` would happily
// build a tree from "a/b/c" (the Files tab relies on that), and a dot-prefixed name would
// be created and then hidden by list_dirs. These rules mirror `invalid_dir_name` in
// src/assistant/workspace.py; tests/test_workspace.py asserts the same table server-side,
// so a rule changed on one side without the other shows up as a failure here or there.

test('accepts ordinary folder names', () => {
  for (const ok of ['reports', 'market roundups', 'Q3-2026', 'a', 'naïve', '日本語', 'x'.repeat(255)]) {
    assert.equal(invalidFolderName(ok), null, `expected ${ok} to be accepted`)
  }
})

test('rejects empty and whitespace-only names', () => {
  assert.equal(invalidFolderName(''), 'Enter a folder name')
  assert.equal(invalidFolderName('   '), 'Enter a folder name')
  assert.equal(invalidFolderName(undefined), 'Enter a folder name')
})

test('rejects surrounding whitespace but allows it inside', () => {
  assert.equal(invalidFolderName(' lead'), "Name can't start or end with a space")
  assert.equal(invalidFolderName('trail '), "Name can't start or end with a space")
  assert.equal(invalidFolderName('two words'), null)
})

test('rejects path separators — a name is not a path', () => {
  assert.equal(invalidFolderName('a/b'), "Name can't contain slashes")
  assert.equal(invalidFolderName('a\\b'), "Name can't contain slashes")
  assert.equal(invalidFolderName('../escape'), "Name can't contain slashes")
})

test('rejects the relative-directory names', () => {
  assert.equal(invalidFolderName('.'), 'Not a valid folder name')
  assert.equal(invalidFolderName('..'), 'Not a valid folder name')
})

test('rejects dotfolders — they would be created then hidden', () => {
  assert.equal(
    invalidFolderName('.hidden'),
    "Names starting with a dot are hidden and won't show here",
  )
})

test('rejects control characters', () => {
  assert.equal(invalidFolderName('nul\x00byte'), 'Name contains invalid characters')
  assert.equal(invalidFolderName('tab\tsep'), 'Name contains invalid characters')
  assert.equal(invalidFolderName('del\x7f'), 'Name contains invalid characters')
})

test('measures length in bytes, not characters', () => {
  assert.equal(invalidFolderName('x'.repeat(256)), 'Name is too long')
  // 128 two-byte chars = 256 bytes: within the character count, over NAME_MAX.
  assert.equal(invalidFolderName('é'.repeat(128)), 'Name is too long')
  assert.equal(invalidFolderName('é'.repeat(127)), null)
})
