import { test } from 'node:test'
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'
import { clearsTreeTarget } from './filesTree.ts'

// The Files-tree body clears the upload/mkdir target on a background click but must
// NOT do so when the click bubbled up from a row — the regression that made granted
// Folders impossible to select as an upload target (folder rows lacked the guard, so
// selecting one was instantly wiped by the tree body's clear-on-click).
function doc() {
  return new JSDOM(`
    <div class="fttree">
      <div class="ftrow ftdir ftfolder"><span class="ftname">web</span>
        <button class="ftcaret"></button>
      </div>
      <div class="ftsection">Folders</div>
      <p class="ftmuted ftempty">No files yet</p>
    </div>`).window.document
}

test('clearsTreeTarget: a click on a folder row — or any child of it — never clears the target (the selection-wipe regression)', () => {
  const d = doc()
  assert.equal(clearsTreeTarget(d.querySelector('.ftrow')), false)          // the row div itself
  assert.equal(clearsTreeTarget(d.querySelector('.ftrow .ftname')), false)  // a child span
  assert.equal(clearsTreeTarget(d.querySelector('.ftrow .ftcaret')), false) // the caret button
})

test('clearsTreeTarget: a click on the tree background (not a row) clears the target', () => {
  const d = doc()
  assert.equal(clearsTreeTarget(d.querySelector('.fttree')), true)     // the scroll container
  assert.equal(clearsTreeTarget(d.querySelector('.ftsection')), true)  // the "Folders" divider
  assert.equal(clearsTreeTarget(d.querySelector('.ftempty')), true)    // the empty-state text
})

test('clearsTreeTarget: a missing or non-element target never wipes the selection', () => {
  assert.equal(clearsTreeTarget(null), false)
  assert.equal(clearsTreeTarget(undefined), false)
  assert.equal(clearsTreeTarget({}), false)
})
