import { test } from 'node:test'
import assert from 'node:assert/strict'
import { viewerKind, previewable, ancestorDirs, iconForFile } from './preview.js'

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

test('iconForFile: maps by kind, extension refinement, and well-known name', () => {
  assert.equal(iconForFile('main.py'), 'file-code')     // code kind
  assert.equal(iconForFile('logo.SVG'), 'file-image')   // image kind, case-insensitive
  assert.equal(iconForFile('data.csv'), 'file-spreadsheet') // ext refinement (kind is 'text')
  assert.equal(iconForFile('bundle.tar.gz'), 'file-archive') // ext refinement (kind is 'download')
  assert.equal(iconForFile('clip.mp4'), 'file-play')    // video → play glyph
  assert.equal(iconForFile('song.MP3'), 'file-music')   // audio, case-insensitive
  assert.equal(iconForFile('Dockerfile'), 'file-code')  // well-known filename, no extension
  assert.equal(iconForFile('index.html'), 'file-code')  // html kind → code icon
  assert.equal(iconForFile('notes.md'), 'file')         // known-but-unrefined → generic
  assert.equal(iconForFile('mystery.xyz'), 'file')      // unknown → generic
  assert.equal(iconForFile(''), 'file')                 // empty → generic
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
