import { test } from 'node:test'
import assert from 'node:assert/strict'
import { viewerKind, previewable, ancestorDirs } from './preview.js'

test('viewerKind: maps extensions to render kinds', () => {
  assert.equal(viewerKind('a.md'), 'markdown')
  assert.equal(viewerKind('a.PNG'), 'image')       // case-insensitive
  assert.equal(viewerKind('a.py'), 'code')
  assert.equal(viewerKind('a.txt'), 'text')
  assert.equal(viewerKind('a.pdf'), 'pdf')
  assert.equal(viewerKind('a.unknownext'), 'download')
  assert.equal(viewerKind(''), 'download')
})

test('previewable: true for known kinds, false for download-only', () => {
  assert.equal(previewable('a.md'), true)
  assert.equal(previewable('a.bin'), false)
})

test('ancestorDirs: nested path yields each ancestor Directory, shallow→deep', () => {
  assert.deepEqual(ancestorDirs('a/b/c.md'), ['a', 'a/b'])
})

test('ancestorDirs: file at the Files-space root has no ancestors', () => {
  assert.deepEqual(ancestorDirs('c.md'), [])
})

test('ancestorDirs: single nesting level yields one ancestor', () => {
  assert.deepEqual(ancestorDirs('docs/notes.txt'), ['docs'])
})

test('ancestorDirs: empty / nullish path yields nothing', () => {
  assert.deepEqual(ancestorDirs(''), [])
  assert.deepEqual(ancestorDirs(null), [])
  assert.deepEqual(ancestorDirs(undefined), [])
})

test('ancestorDirs: does not include the file path itself', () => {
  assert.ok(!ancestorDirs('a/b/c.md').includes('a/b/c.md'))
})
