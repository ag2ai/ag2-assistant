<script module>
  // Session-scoped tree state that must survive the component remounting each time
  // the Files tab is (re)activated: which Directories the user has expanded
  // (default is collapsed) and the current upload-target selection. A profile
  // switch is a full-page nav, so these reset with it — correct.
  let sessionExpanded = new Set()
  let sessionSelected = ''
  // Which granted-Folder Directories the user has expanded (absolute paths, kept apart
  // from the Files-space `sessionExpanded` so the Files-space reconcile never prunes
  // them). Module-scoped to survive the tab-remount; re-scoped when the Thread changes.
  let sessionFolderExpanded = new Set()
  // The last Reveal request (store `epoch`) this tab has acted on. Module-scoped so it
  // survives the remount on tab (re)activation — otherwise a lingering reveal request
  // would re-fire every time the Files tab is opened normally.
  let handledRevealEpoch = 0
</script>

<script>
  // The profile's user-writable Files space rendered as an IDE-style Directory
  // tree (ADR 0007). Built client-side from the flat {files, dirs} listing by
  // splitting paths. Freshness is pull: loads when the tab opens and on ↻ refresh,
  // never a background poll. Supports upload (drag OS files or ⤒), New directory,
  // rename/move (inline editor or drag a row onto a Directory), and recursive
  // Directory delete — alongside the agent's own writes.
  import { onMount, tick, untrack } from 'svelte'
  import { openAsideFile, closeAside, route } from '../router.js'
  import { reveal } from '../store.js'
  import { threadScope } from '../lib/threadScope.js'
  import { foldersStore } from '../lib/folders.js'
  import { api } from '../transport/api.js'
  import { ancestorDirs, iconForFile } from '../lib/preview.js'
  import { modeLabel, isFolderPath, folderAncestorDirs, folderAffordances } from '../lib/folderFiles.js'
  import { clearsTreeTarget } from '../lib/filesTree.js'
  import Icon from './Icon.svelte'

  let files = $state([])          // flat [{path,name,dir,size,modified}]
  let dirs = $state([])           // flat [relpath] — includes empty Directories
  let root = $state('')
  let loading = $state(true)
  let err = $state('')

  // Expanded Directories (a Directory is collapsed unless present here); selected
  // upload-target Directory ('' = Files-space root). Seeded from session state so
  // switching tabs preserves the tree's shape.
  let expanded = $state(sessionExpanded)
  let selected = $state(sessionSelected)
  $effect(() => { sessionSelected = selected })

  let treeEl                      // the scroll container, for scrolling a revealed row in
  // Coalescing is scoped to the mount burst ONLY: onMount's load and a Reveal fired at
  // the same tab-open share a single request (per the Reveal decision — don't
  // double-fetch). Every post-mount caller (↻ refresh, delete/move/mkdir/upload, and a
  // Reveal into an already-open tab) forces its own fresh pull, so freshness is never
  // traded away — a Reveal is guaranteed to see a just-written file.
  let mounted = false
  let inflight = null
  async function load() {
    if (!mounted && inflight) return inflight
    loading = true
    err = ''
    const p = (async () => {
      try {
        const r = await api.files()
        files = r.files || []
        dirs = r.dirs || []
        root = r.root || ''
        reconcile()   // drop selection/expansion pointing at Directories that no longer exist
      } catch (e) {
        err = String(e.message || e)
      }
      loading = false
    })()
    inflight = p
    try { await p } finally { if (inflight === p) inflight = null }
  }

  // Prune stale references to Directories that vanished (deleted here, or removed
  // out-of-band): a dangling `selected` would otherwise redirect New directory /
  // Upload into a phantom path. When the selected Directory is gone, fall back to
  // its nearest surviving ancestor (not the root) so recreating what was just
  // deleted lands back in the same place instead of colliding at the root.
  // `dirs` is the authoritative set of existing paths.
  function reconcile() {
    const live = new Set(dirs)
    // A Folder (absolute) target lives in the Thread-scoped section, not `dirs`, so the
    // Files-space reconcile leaves it alone (soft-degrades on its own if it vanishes).
    if (selected && !isFolderPath(selected) && !live.has(selected)) {
      let p = selected
      do { p = p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '' } while (p && !live.has(p))
      selected = p
    }
    const kept = [...expanded].filter((p) => live.has(p))
    if (kept.length !== expanded.size) { expanded = new Set(kept); sessionExpanded = expanded }
  }
  // After the first load resolves, the mount burst is over: coalescing turns off so
  // every later load is a guaranteed-fresh fetch.
  onMount(async () => { await load(); mounted = true })

  // ---- Tree assembly (directories-first, alphabetical, from the flat lists) ----
  const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })
  const tree = $derived.by(() => {
    const rootNode = { name: '', path: '', dirs: new Map(), files: [] }
    const ensure = (path) => {
      let cur = rootNode
      if (!path) return cur
      let acc = ''
      for (const part of path.split('/')) {
        acc = acc ? acc + '/' + part : part
        if (!cur.dirs.has(part)) cur.dirs.set(part, { name: part, path: acc, dirs: new Map(), files: [] })
        cur = cur.dirs.get(part)
      }
      return cur
    }
    for (const d of dirs) ensure(d)
    for (const f of files) ensure(f.dir).files.push(f)
    return rootNode
  })
  const subDirs = (node) => [...node.dirs.values()].sort(byName)
  const subFiles = (node) => [...node.files].sort(byName)
  const isEmpty = $derived(!files.length && !dirs.length)

  const isOpen = (path) => expanded.has(path)
  function toggle(path) {
    if (expanded.has(path)) expanded.delete(path)
    else expanded.add(path)
    expanded = new Set(expanded)   // reassign → reactive
    sessionExpanded = expanded
  }

  // Files in (and under) a Directory — for the recursive-delete confirm count.
  const countUnder = (path) => files.filter((f) => f.path === path || f.path.startsWith(path + '/')).length

  // ---- Open (view/download) ----
  const fmtSize = (n) =>
    n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`
  // The Active file's path (the row highlighted to match the preview rail). Read
  // off the URL's aside slot, not a local flag, so it tracks Back/Forward,
  // chat-opened deliverables, and refresh for free; null when the rail is closed
  // or holds the Inspector.
  const activePath = $derived($route.aside?.kind === 'file' ? $route.aside.path : null)
  // The Active file is in the current listing (a path-less preview marks no row).
  const activeInTree = $derived(!!activePath && files.some((f) => f.path === activePath))
  // The shallowest collapsed ancestor Directory of the Active file: the visible
  // folder that stands in for the hidden file and wears the active pill in its
  // place. Null when every ancestor is expanded, i.e. the file's own row shows.
  const activeDir = $derived.by(() => {
    if (!activeInTree) return null
    let acc = ''
    const parts = activePath.split('/')
    parts.pop()   // drop the filename; only ancestor Directories gate visibility
    for (const p of parts) {
      acc = acc ? acc + '/' + p : p
      if (!expanded.has(acc)) return acc
    }
    return null
  })
  // The Active file's own row is rendered (passive reveal — all ancestors open).
  const activeVisible = $derived(activeInTree && activeDir === null)
  // Open the file in the preview rail; unpreviewable types offer a download there.
  // Clicking the already-Active file toggles the rail shut.
  function openFile(f) {
    if (f.path === activePath) closeAside()
    else openAsideFile(f.path)
  }

  // ---- Granted Folders (a Thread-scoped section beneath the Files-space tree, ADR 0013) ----
  // The Folder roots reachable in the OPEN THREAD (chat/task overrides/blocks applied),
  // lazy-expanded one Directory level at a time. `chatId` is the Thread's scope token
  // (lib/threadScope.js; '' → profile grants only) and re-scopes the section on a switch.
  const chatId = $derived($threadScope)
  let folderRoots = $state([])                 // [{id,name,path,mode,exists}]
  let folderErr = $state('')
  let folderLevels = $state(new Map())         // abs dir path -> {dirs:[{name,path}], files:[{name,path,size}], err?}
  let folderExpanded = $state(sessionFolderExpanded)
  let folderLoading = $state(new Set())

  async function loadFolderRoots() {
    folderErr = ''
    try {
      const r = await api.folderRoots(chatId)
      folderRoots = r.roots || []
    } catch (e) {
      folderErr = String(e.message || e)
      folderRoots = []
    }
  }
  // Fetch one Directory level; a moved/revoked path soft-degrades to an empty, flagged
  // level (the rail's "not reachable" philosophy, ADR 0012) rather than throwing.
  async function loadFolderLevel(path) {
    folderLoading.add(path); folderLoading = new Set(folderLoading)
    try {
      const r = await api.folderList(path, chatId)
      // `mode` is THIS level's own resolved Grant mode (server-side), so its rows'
      // affordances track the Grant that actually covers this Directory, not just the
      // hosting root's (ticket 04).
      folderLevels.set(path, { dirs: r.dirs || [], files: r.files || [], mode: r.mode || '' })
    } catch {
      folderLevels.set(path, { dirs: [], files: [], err: true })
    } finally {
      folderLevels = new Map(folderLevels)
      folderLoading.delete(path); folderLoading = new Set(folderLoading)
    }
  }
  function toggleFolder(path) {
    if (folderExpanded.has(path)) folderExpanded.delete(path)
    else { folderExpanded.add(path); if (!folderLevels.has(path)) loadFolderLevel(path) }
    folderExpanded = new Set(folderExpanded)
    sessionFolderExpanded = folderExpanded
  }
  // Re-pull the Folder section after a Folder mutation (delete/rename/move/mkdir/upload,
  // tickets 04–05): refresh the roots (a mode may have changed) and every currently-
  // expanded level IN PLACE, so the mutated file appears/disappears without collapsing
  // the tree. The Files-space `load()` never touches these levels, and vice-versa.
  async function reloadFolders() {
    await loadFolderRoots()
    for (const p of folderExpanded) await loadFolderLevel(p)
  }
  // Re-scope on Thread switch: re-resolve the roots against the new chat's grants and
  // drop every cached level (a root may have vanished or changed mode), then re-hydrate
  // the still-expanded Directories. `chatId` is the SOLE tracked dependency — the rest is
  // untracked so a plain expand/collapse (which mutates folderExpanded/folderLevels) never
  // re-fires this and wipes the tree.
  $effect(() => {
    chatId
    untrack(() => {
      folderLevels = new Map()
      loadFolderRoots()
      for (const p of folderExpanded) loadFolderLevel(p)
    })
  })
  // The Folder registry + Grants are shared (lib/folders.js): flipping a grant from the
  // ChatFolders modal, Settings, or the composer pushes a new snapshot to `foldersStore`.
  // Re-resolve THIS Thread's Folder section against it so the roots' mode badges and the
  // rows' write affordances track the change live, not just on a Thread switch (ADR 0013).
  // First run is skipped (onMount + the chatId effect already load); untrack inside so a
  // plain expand/collapse never re-fires it. reloadFolders() never writes the store — no loop.
  let foldersHydrated = false
  $effect(() => {
    $foldersStore
    if (!foldersHydrated) { foldersHydrated = true; return }
    untrack(() => reloadFolders())
  })

  // ---- Reveal (locate the Active file where it lives) ----
  // React to a Reveal request from the preview header: pull a fresh listing (so a
  // just-written file is present), persistently expand the file's ancestor Directories
  // (as if the user clicked each chevron), then scroll its highlighted row into view.
  // The `epoch` nonce re-fires this even for a repeat Reveal of the same path; the
  // module-scoped guard stops a lingering request re-firing on a plain tab (re)open.
  $effect(() => {
    const { path, kind, epoch } = $reveal
    if (!path || epoch === handledRevealEpoch) return
    handledRevealEpoch = epoch
    revealInTree(path, kind)
  })
  async function revealInTree(path, kind) {
    // A Folder (absolute) file/directory lives in the Thread-scoped Folder section,
    // not the Files-space tree; expand it there instead.
    if (isFolderPath(path)) return revealInFolder(path, kind)
    for (const d of ancestorDirs(path)) expanded.add(d)   // from the path string; no listing needed
    // A directory reveal also opens the directory itself, so its contents show and the
    // row it stands on is the one we scroll to (a file's own row is the scroll target).
    // Selecting it makes it the upload/mkdir target too, and highlights the row.
    if (kind === 'directory') { expanded.add(path); selected = path }
    expanded = new Set(expanded)
    sessionExpanded = expanded
    await load()          // coalesces with onMount's load when the tab just opened
    await tick()          // let the expanded rows render before we measure
    scrollRevealed(path, kind)
  }
  // Reveal a Folder file/directory: re-resolve this Thread's roots, expand the hosting
  // root's ancestors down to it (each level pulled fresh), scroll it in; no host → no-op.
  // A directory reveal also expands the directory itself so its listing is visible.
  async function revealInFolder(path, kind) {
    await loadFolderRoots()
    let dirs = null
    for (const r of folderRoots) {
      if (!r.exists) continue
      // A directory names itself; folderAncestorDirs stops at the parent, so append the
      // directory so it (and, once loaded, its contents) is expanded too. The hosting
      // root mentioned directly resolves to just [root].
      if (r.path === path) { dirs = [r.path]; break }
      const d = folderAncestorDirs(r.path, path)
      if (d.length) { dirs = kind === 'directory' ? [...d, path] : d; break }
    }
    if (!dirs) return
    for (const d of dirs) folderExpanded.add(d)
    folderExpanded = new Set(folderExpanded)
    sessionFolderExpanded = folderExpanded
    if (kind === 'directory') selected = path   // highlight it + make it the mutation target
    for (const d of dirs) await loadFolderLevel(d)
    await tick()
    scrollRevealed(path, kind)
  }
  // Scroll the revealed row into view: a file rides the active pill (.ftrow.active); a
  // directory has no active state, so target its row by path.
  function scrollRevealed(path, kind) {
    if (kind === 'directory') {
      for (const el of treeEl?.querySelectorAll('[data-path]') || [])
        if (el.dataset.path === path) return void el.scrollIntoView({ block: 'nearest' })
      return
    }
    treeEl?.querySelector('.ftrow.active')?.scrollIntoView({ block: 'nearest' })
  }

  // ---- Selection (upload target) ----
  const selectDir = (path) => { selected = path }
  // The tree body clears the target only on a BACKGROUND click, not one that bubbled
  // up from a row — one guard in the surface that owns the rule (mirroring
  // onDocPointer's closest() menu check), so no row handler needs its own
  // stopPropagation and a new row type can't silently reintroduce the wipe.
  const onTreeBodyClick = (e) => { if (clearsTreeTarget(e.target)) selected = '' }

  // Scroll a just-created Directory / just-uploaded file into view by its path
  // (block:'nearest' → only when it's off-screen). Rows carry `data-path`; matched
  // exactly, so odd characters in a path need no CSS.escape. No-op if the row isn't
  // rendered (ancestor collapsed / not yet loaded).
  function revealRow(path) {
    for (const el of treeEl?.querySelectorAll('[data-path]') || [])
      if (el.dataset.path === path) return void el.scrollIntoView({ block: 'nearest' })
  }

  // ---- Row kebab menu (one at a time, fixed-positioned so the scroll can't clip) ----
  let menu = $state('')            // node path whose menu is open
  let menuAnchor = $state(null)    // kebab rect the open menu is positioned against
  function toggleMenu(e, path) {
    e.stopPropagation()
    if (menu === path) { menu = ''; return }
    const r = e.currentTarget.getBoundingClientRect()
    menuAnchor = { top: r.top, bottom: r.bottom, left: r.left, right: r.right }
    menu = path
  }
  // Place the menu against the kebab, then flip/clamp it so it never spills off a
  // screen edge: right-aligned by default, opening upward when the bottom is tight.
  function positionMenu(el, anchor) {
    const m = 8  // keep this much gap from every viewport edge
    const place = (a) => {
      if (!a) return
      const { width: w, height: h } = el.getBoundingClientRect()
      const vw = window.innerWidth, vh = window.innerHeight
      let left = a.right - w                       // right edge aligns with the kebab
      if (left < m) left = a.left                  // too tight on the left → open rightward
      left = Math.max(m, Math.min(left, vw - w - m))
      let top = a.bottom + 4                        // open below by default
      if (top + h > vh - m && a.top - 4 - h >= m) top = a.top - 4 - h  // flip above
      top = Math.max(m, Math.min(top, vh - h - m))
      el.style.left = `${left}px`
      el.style.top = `${top}px`
      el.style.transform = 'none'
    }
    place(anchor)
    return { update: place }
  }
  function onDocPointer(e) {
    if (menu && !e.target.closest('.ftmenu') && !e.target.closest('.ftkebab')) menu = ''
  }
  function onDocKey(e) {
    if (e.key === 'Escape') { menu = ''; if (renaming) cancelRename(); if (creating) creating = false }
  }
  onMount(() => {
    document.addEventListener('pointerdown', onDocPointer, true)
    document.addEventListener('keydown', onDocKey)
    return () => {
      document.removeEventListener('pointerdown', onDocPointer, true)
      document.removeEventListener('keydown', onDocKey)
    }
  })

  // ---- Delete (file or Directory, recursive) ----
  let confirming = $state('')      // path awaiting delete confirmation
  let busy = $state('')            // path currently mutating
  async function del(path) {
    busy = path
    try {
      await api.deleteFile(path, chatId)
      if (isFolderPath(path)) await reloadFolders()
      else await load()
    } catch (e) { err = String(e.message || e) }
    busy = ''
    confirming = ''
  }

  // ---- Rename / move via inline editor ----
  // The editor accepts a bare name (rename in place) or a relative path (move,
  // creating intermediate Directories). parentOf gives the containing Directory.
  let renaming = $state('')        // path being renamed
  let renameText = $state('')
  const parentOf = (path) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
  function startRename(path, name) {
    menu = ''
    renaming = path
    renameText = name
  }
  function cancelRename() { renaming = '' }
  async function commitRename(fromPath) {
    if (renaming !== fromPath) return    // already cancelled (Escape) — ignore blur
    const t = renameText.trim()
    renaming = ''
    if (!t) return
    const to = t.includes('/') ? t : (parentOf(fromPath) ? parentOf(fromPath) + '/' + t : t)
    if (to === fromPath) return
    await doMove(fromPath, to)
  }
  async function doMove(from, to) {
    busy = from
    try {
      await api.moveFile(from, to, chatId)
      // Follow the Active preview to its new path: renaming/moving the file the rail
      // shows must retarget it (title + URL), not strand it on the vanished path.
      // A moved Directory carries its active descendant along by prefix.
      if (activePath === from) openAsideFile(to)
      else if (activePath && activePath.startsWith(from + '/')) openAsideFile(to + activePath.slice(from.length))
      if (isFolderPath(from)) await reloadFolders()
      else await load()
    } catch (e) { err = String(e.message || e) }   // 409 clash surfaces its message
    busy = ''
  }
  function focusSelect(node) { node.focus(); node.select() }

  // ---- New Directory ----
  let creating = $state(false)
  let newName = $state('')
  function startCreate() { creating = true; newName = '' }
  async function commitCreate() {
    if (!creating) return    // Enter already handled it — ignore the blur that follows unmount
    creating = false
    const t = newName.trim()
    if (!t) return
    const path = selected ? selected + '/' + t : t
    busy = path
    try {
      await api.mkdir(path, chatId)
      if (isFolderPath(path)) {
        folderExpanded.add(selected); folderExpanded = new Set(folderExpanded)  // reveal the new child
        await reloadFolders()
      } else {
        if (selected) { expanded.add(selected); expanded = new Set(expanded) }  // reveal the new child
        await load()
      }
      // Make the new Directory the next target and bring it into view, so an
      // immediate upload / nested mkdir lands inside it without a second click.
      selected = path
      await tick()
      revealRow(path)
    } catch (e) { err = String(e.message || e) }
    busy = ''
  }

  // ---- Upload (⤒ picker or OS drag-drop), auto-suffixed server-side ----
  let fileInput
  async function upload(fileList, targetDir) {
    if (!fileList || !fileList.length) return
    busy = 'upload'
    try {
      const res = await api.uploadFiles(fileList, targetDir || '', chatId)
      const saved = res?.saved || []
      if (isFolderPath(targetDir)) {
        folderExpanded.add(targetDir); folderExpanded = new Set(folderExpanded)  // reveal the drop dir
        await reloadFolders()
      } else {
        if (targetDir) { expanded.add(targetDir); expanded = new Set(expanded) }  // reveal the drop dir
        await load()
      }
      // Select the first uploaded file the way a file CAN be selected — make it the
      // Active file (open it in the preview rail, highlighting its row) and scroll it
      // in. A file isn't a valid upload target, so `selected` stays on the directory.
      // A Folder upload's `saved` path is relative to the Folder ROOT, but the file
      // lands in `targetDir`, so its row path is targetDir + the (suffixed) basename.
      const first = saved[0]
      if (first) {
        const rowPath = isFolderPath(targetDir)
          ? targetDir.replace(/\/+$/, '') + '/' + first.split('/').pop()
          : first
        openAsideFile(rowPath)
        await tick()
        revealRow(rowPath)
      }
    } catch (e) { err = String(e.message || e) }
    busy = ''
  }
  function onPick(e) {
    upload(e.target.files, selected)
    e.target.value = ''            // let the same file be re-picked later
  }

  // ---- Drag & drop: OS files → upload; an internal row → move ----
  const DRAG_TYPE = 'application/x-ag2-path'
  let dropTarget = $state(null)    // Directory path currently hovered as a drop target ('' = root)
  function onRowDragStart(e, path) {
    e.dataTransfer.setData(DRAG_TYPE, path)
    e.dataTransfer.effectAllowed = 'move'
  }
  function onDirDragOver(e, path) {
    e.preventDefault()
    e.stopPropagation()
    dropTarget = path
  }
  function onRootDragOver(e) { e.preventDefault(); dropTarget = '' }
  // `targetDir` is a Directory path when a row is the drop target, or null for the
  // tree body. Body drop: OS files go into the selected Directory (root if none,
  // per Ticket 02); an internal row moves to the root.
  async function onDrop(e, targetDir) {
    e.preventDefault()
    e.stopPropagation()
    dropTarget = null
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      await upload(e.dataTransfer.files, targetDir ?? selected)   // OS files → upload
      return
    }
    const from = e.dataTransfer.getData(DRAG_TYPE)                // internal row → move
    if (!from) return
    const dir = targetDir ?? ''
    const name = from.includes('/') ? from.slice(from.lastIndexOf('/') + 1) : from
    const to = dir ? dir + '/' + name : name
    if (to === from) return
    await doMove(from, to)
  }
</script>

<!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
<div class="ftwrap">
  <div class="fttoolbar">
    <span class="ftsel" title="Upload / New directory target">
    {#if selected}
        {selected}
    {:else}
        Search
    {/if}
    </span>

    <span class="ftspacer"></span>

    <button class="fttool" title="Upload into {selected || 'root'}" aria-label="Upload" onclick={() => fileInput.click()}><Icon name="plus" size={15} /></button>
    <button class="fttool" title="New directory in {selected || 'root'}" aria-label="New directory" onclick={startCreate}><Icon name="folder-plus" size={15} /></button>
    <button class="fttool" title="Refresh" aria-label="Refresh" onclick={load}><Icon name="rotate-cw" size={15} /></button>

    <input type="file" multiple bind:this={fileInput} onchange={onPick} hidden />
  </div>

  {#if err}<p class="fterr">{err} <button class="ftlink" onclick={() => (err = '')}>dismiss</button></p>{/if}

  <!-- The tree body doubles as the root drop zone / clear-target surface (a
       background click clears the upload target; a row click is guarded out). -->
  <div
    class="fttree"
    bind:this={treeEl}
    class:droproot={dropTarget === ''}
    onclick={onTreeBodyClick}
    ondragover={onRootDragOver}
    ondrop={(e) => onDrop(e, null)}
  >
    {#if creating}
      <div class="ftrow ftnew" style="padding-left:8px">
        <Icon name="folder" size={14} />
        <input class="ftinput" placeholder="New directory name" bind:value={newName} use:focusSelect
          onclick={(e) => e.stopPropagation()}
          onkeydown={(e) => { if (e.key === 'Enter') commitCreate(); else if (e.key === 'Escape') creating = false }}
          onblur={commitCreate} />
      </div>
    {/if}

    {#if loading}
      <p class="ftmuted">Loading…</p>
    {:else if isEmpty}
      <p class="ftmuted ftempty">No files yet — ask the agent to save something, run a task that produces a deliverable, or drag files here (or use&nbsp;+) to upload.</p>
    {:else}
      {@render level(tree, 0)}
    {/if}

    <!-- Granted Folders (Thread-scoped, ADR 0013): a distinct section beneath the
         Files-space tree, each root badged with its mode + missing state, lazy-expanded. -->
    {#if folderErr}<p class="fterr">{folderErr} <button class="ftlink" onclick={() => (folderErr = '')}>dismiss</button></p>{/if}
    {#if folderRoots.length}
      <div class="ftsection">Folders</div>
      {#each folderRoots as r (r.path)}
        {@render folderRoot(r)}
      {/each}
    {/if}
  </div>

  <p class="ftcaption" title={root}>{root}</p>
</div>

{#snippet level(node, depth)}
  {#each subDirs(node) as d (d.path)}
    <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
    <div
      class="ftrow ftdir"
      data-path={d.path}
      class:active={d.path === activeDir}
      class:selected={selected === d.path && !activeVisible && d.path !== activeDir}
      class:drop={dropTarget === d.path}
      style="padding-left:{depth * 14 + 4}px"
      draggable="true"
      ondragstart={(e) => onRowDragStart(e, d.path)}
      ondragover={(e) => onDirDragOver(e, d.path)}
      ondrop={(e) => onDrop(e, d.path)}
      onclick={() => { selectDir(d.path); toggle(d.path) }}
    >
      <button class="ftcaret" title={isOpen(d.path) ? 'Collapse' : 'Expand'}
        onclick={(e) => { e.stopPropagation(); toggle(d.path) }}>
        <Icon name={isOpen(d.path) ? 'chevron-down' : 'chevron-right'} size={13} />
      </button>
      <Icon name="folder" size={14} />
      {#if renaming === d.path}
        <input class="ftinput" bind:value={renameText} use:focusSelect
          onclick={(e) => e.stopPropagation()}
          onkeydown={(e) => { if (e.key === 'Enter') commitRename(d.path); else if (e.key === 'Escape') cancelRename() }}
          onblur={() => commitRename(d.path)} />
      {:else}
        <span class="ftname">{d.name}</span>
      {/if}
      {@render rowActions(d.path, d.name, true)}
    </div>
    {#if isOpen(d.path)}
      {@render level(d, depth + 1)}
    {/if}
  {/each}

  {#each subFiles(node) as f (f.path)}
    <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
    <div
      class="ftrow ftfile"
      data-path={f.path}
      class:active={activePath === f.path}
      style="padding-left:{depth * 14 + 24}px"
      draggable="true"
      ondragstart={(e) => onRowDragStart(e, f.path)}
      onclick={() => { if (renaming !== f.path) openFile(f) }}
    >
      <Icon name={iconForFile(f.name)} size={14} />
      {#if renaming === f.path}
        <input class="ftinput" bind:value={renameText} use:focusSelect
          onclick={(e) => e.stopPropagation()}
          onkeydown={(e) => { if (e.key === 'Enter') commitRename(f.path); else if (e.key === 'Escape') cancelRename() }}
          onblur={() => commitRename(f.path)} />
      {:else}
        <span class="ftname" title={f.path}>{f.name}</span>
        <span class="ftmeta">{fmtSize(f.size)}</span>
      {/if}
      {@render rowActions(f.path, f.name, false)}
    </div>
  {/each}
{/snippet}

{#snippet folderRoot(r)}
  {@const aff = folderAffordances(r.mode)}
  <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
  <div
    class="ftrow ftdir ftfolder"
    class:missing={!r.exists}
    class:drop={dropTarget === r.path}
    class:selected={r.exists && selected === r.path}
    style="padding-left:4px"
    title={r.path}
    ondragover={aff.move && r.exists ? (e) => onDirDragOver(e, r.path) : null}
    ondrop={aff.move && r.exists ? (e) => onDrop(e, r.path) : null}
    onclick={() => { if (r.exists) { if (aff.move) selectDir(r.path); toggleFolder(r.path) } }}
  >
    <button class="ftcaret" title={folderExpanded.has(r.path) ? 'Collapse' : 'Expand'}
      disabled={!r.exists} onclick={(e) => { e.stopPropagation(); if (r.exists) toggleFolder(r.path) }}>
      <Icon name={folderExpanded.has(r.path) ? 'chevron-down' : 'chevron-right'} size={13} />
    </button>
    <Icon name="folder" size={14} />
    <span class="ftname">{r.name}</span>
    {#if modeLabel(r.mode)}<span class="ftbadge" class:rw={r.mode === 'read_write'}>{modeLabel(r.mode)}</span>{/if}
    {#if !r.exists}<span class="ftbadge warn" title="This folder's path no longer exists — repoint it in Settings → Folders">missing</span>{/if}
  </div>
  {#if r.exists && folderExpanded.has(r.path)}
    {@render folderLevel(r.path, 1, r.mode)}
  {/if}
{/snippet}

<!-- A Folder Directory level (lazy-loaded). `mode` is the hosting root's resolved Grant
     mode threaded down: under a read_write Grant its rows gain the full mutation set
     (rename/delete/move + drop target), matching the Files-space tree; a read root shows
     none — preview/download only (ticket 04). The server enforces the same truth. -->
{#snippet folderLevel(path, depth, mode)}
  {@const lvl = folderLevels.get(path)}
  <!-- Prefer this level's own server-resolved mode; fall back to the parent's until the
       listing loads. So a nested Directory whose Grant differs from the root resolves
       its own affordances (ticket 04). -->
  {@const aff = folderAffordances(lvl?.mode ?? mode)}
  {#if !lvl && folderLoading.has(path)}
    <p class="ftmuted" style="padding-left:{depth * 14 + 24}px">Loading…</p>
  {:else if lvl}
    {#each lvl.dirs as d (d.path)}
      <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
      <div class="ftrow ftdir" data-path={d.path} class:drop={dropTarget === d.path} class:selected={selected === d.path}
        style="padding-left:{depth * 14 + 4}px" title={d.path}
        draggable={aff.move}
        ondragstart={aff.move ? (e) => onRowDragStart(e, d.path) : null}
        ondragover={aff.move ? (e) => onDirDragOver(e, d.path) : null}
        ondrop={aff.move ? (e) => onDrop(e, d.path) : null}
        onclick={() => { if (renaming !== d.path) { if (aff.move) selectDir(d.path); toggleFolder(d.path) } }}>
        <button class="ftcaret" title={folderExpanded.has(d.path) ? 'Collapse' : 'Expand'}
          onclick={(e) => { e.stopPropagation(); toggleFolder(d.path) }}>
          <Icon name={folderExpanded.has(d.path) ? 'chevron-down' : 'chevron-right'} size={13} />
        </button>
        <Icon name="folder" size={14} />
        {#if renaming === d.path}
          <input class="ftinput" bind:value={renameText} use:focusSelect
            onclick={(e) => e.stopPropagation()}
            onkeydown={(e) => { if (e.key === 'Enter') commitRename(d.path); else if (e.key === 'Escape') cancelRename() }}
            onblur={() => commitRename(d.path)} />
        {:else}
          <span class="ftname">{d.name}</span>
        {/if}
        {#if aff.rename || aff.delete}{@render rowActions(d.path, d.name, true)}{/if}
      </div>
      {#if folderExpanded.has(d.path)}
        {@render folderLevel(d.path, depth + 1, lvl?.mode ?? mode)}
      {/if}
    {/each}
    {#each lvl.files as f (f.path)}
      <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
      <div class="ftrow ftfile" data-path={f.path} class:active={activePath === f.path}
        style="padding-left:{depth * 14 + 24}px" title={f.path}
        draggable={aff.move}
        ondragstart={aff.move ? (e) => onRowDragStart(e, f.path) : null}
        onclick={() => { if (renaming !== f.path) openFile(f) }}>
        <Icon name={iconForFile(f.name)} size={14} />
        {#if renaming === f.path}
          <input class="ftinput" bind:value={renameText} use:focusSelect
            onclick={(e) => e.stopPropagation()}
            onkeydown={(e) => { if (e.key === 'Enter') commitRename(f.path); else if (e.key === 'Escape') cancelRename() }}
            onblur={() => commitRename(f.path)} />
        {:else}
          <span class="ftname">{f.name}</span>
          <span class="ftmeta">{fmtSize(f.size)}</span>
        {/if}
        {#if aff.rename || aff.delete}{@render rowActions(f.path, f.name, false)}{/if}
      </div>
    {/each}
    {#if !lvl.dirs.length && !lvl.files.length}
      <p class="ftmuted" style="padding-left:{depth * 14 + 24}px">{lvl.err ? 'Not reachable' : 'Empty'}</p>
    {/if}
  {/if}
{/snippet}

{#snippet rowActions(path, name, isDir)}
  {#if confirming === path}
    <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
    <span class="ftconfirm" onclick={(e) => e.stopPropagation()}>
      <span class="confirm">Delete{#if isDir}{#if isFolderPath(path)} this folder{:else} {countUnder(path)} file{countUnder(path) === 1 ? '' : 's'}{/if}{/if}?</span>
      <button class="ftlink danger" disabled={busy === path} onclick={(e) => { e.stopPropagation(); del(path) }}>{busy === path ? '…' : 'yes'}</button>
      <button class="ftlink" onclick={(e) => { e.stopPropagation(); confirming = '' }}>no</button>
    </span>
  {:else if renaming !== path}
    <button class="ftkebab" title="Actions" aria-haspopup="menu" aria-expanded={menu === path}
      onclick={(e) => toggleMenu(e, path)}><Icon name="ellipsis-vertical" size={14} /></button>
    {#if menu === path}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <div class="ftmenu" role="menu" tabindex="-1" use:positionMenu={menuAnchor} onclick={(e) => e.stopPropagation()}>
        {#if !isDir}
          <a class="ftmitem" role="menuitem" href={api.fileUrl(path, true, chatId)} onclick={() => (menu = '')}>
            <Icon name="download" size={14} /> Download
          </a>
        {/if}
        <button class="ftmitem" role="menuitem" onclick={() => startRename(path, name)}>
          <Icon name="pencil" size={14} /> Rename
        </button>
        <div class="ftmdiv"></div>
        <!-- Files and empty Files-space Directories delete immediately; a Directory with
             files in it (or any Folder Directory, whose count we haven't listed) confirms
             first (recursive delete). -->
        <button class="ftmitem danger" role="menuitem" onclick={() => { menu = ''; if (isDir && (isFolderPath(path) || countUnder(path))) confirming = path; else del(path) }}>
          <Icon name="trash" size={14} /> Delete
        </button>
      </div>
    {/if}
  {/if}
{/snippet}

<style>
  .ftwrap { display: flex; flex-direction: column; flex: 1; min-height: 0; }
  .fttoolbar { display: flex; align-items: center; gap: 2px; padding: 6px 8px; border-bottom: 1px solid var(--line); }
  .fttool { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border: none; background: none; color: var(--muted); border-radius: 7px; cursor: pointer; }
  .fttool:hover { color: var(--text); background: var(--code); }
  .ftspacer { flex: 1; }
  .ftsel { max-width: 60%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--mono); font-size: 11px; color: var(--accent); }

  .fterr { margin: 6px 8px 0; color: var(--danger); font-size: 12px; }
  .ftlink { border: none; background: none; color: var(--accent); font: inherit; font-size: 12px; cursor: pointer; padding: 0; }
  .ftlink:hover { text-decoration: underline; }
  .ftlink.danger { color: var(--muted); }
  .ftlink.danger:hover { color: var(--danger); }
  .ftlink.danger:disabled { cursor: default; opacity: .6; }

  .fttree { flex: 1; overflow-y: auto; padding: 4px 0; min-height: 0; }
  .fttree.droproot { outline: 2px dashed var(--accent); outline-offset: -3px; border-radius: 6px; }

  .ftrow { position: relative; display: flex; align-items: center; gap: 6px; padding: 4px 8px 4px 4px; cursor: pointer; color: var(--text); user-select: none; }
  .ftrow:hover { background: var(--surface-hover, var(--code)); }
  .ftdir.selected { background: color-mix(in srgb, var(--accent) 16%, transparent); }
  /* The active row — the Active file's own row, or the folder standing in for it
     when that file is collapsed out of view — is a full-width green row: the same
     --accent-soft fill + left accent bar as the active chat/task row (.drow.on),
     edge to edge. After :hover, so the fill holds on hover. */
  .ftrow.active { background: var(--accent-soft); box-shadow: inset 2px 0 0 var(--accent); }
  .ftrow.drop { outline: 2px dashed var(--accent); outline-offset: -2px; border-radius: 6px; }
  .ftcaret { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px; margin-left: -2px; border: none; background: none; color: var(--muted); cursor: pointer; border-radius: 4px; }
  .ftcaret:hover { color: var(--text); }
  .ftname { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; }
  .ftfile .ftname { font-family: var(--mono); }
  .ftmeta { flex: none; color: var(--muted); font-size: 11px; white-space: nowrap; }
  .ftinput { flex: 1; min-width: 0; font: inherit; font-size: 13px; padding: 1px 5px; border: 1px solid var(--accent); border-radius: 5px; background: var(--surface); color: var(--text); }

  .ftconfirm { flex: none; display: inline-flex; align-items: center; gap: 7px; margin-left: auto; }
  .ftconfirm .confirm { color: var(--danger); font-size: 12px; white-space: nowrap; }

  .ftkebab { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; margin-left: auto; border: none; background: none; color: var(--muted); border-radius: 5px; opacity: 0; cursor: pointer; }
  .ftrow:hover .ftkebab, .ftrow:focus-within .ftkebab { opacity: .55; }
  .ftkebab:hover { opacity: 1; color: var(--text); }
  .ftmenu { position: fixed; z-index: var(--z-modal); min-width: 150px; padding: 5px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; box-shadow: var(--shadow, 0 8px 28px rgba(0,0,0,.18)); }
  .ftmitem { display: flex; align-items: center; gap: 8px; width: 100%; padding: 7px 9px; border: none; background: none; color: var(--text); font: inherit; font-size: 13px; text-align: left; text-decoration: none; border-radius: 6px; cursor: pointer; }
  .ftmitem:hover { background: var(--surface-hover, var(--code)); }
  .ftmitem.danger { color: var(--danger); }
  .ftmdiv { height: 1px; margin: 4px 6px; background: var(--line); }

  .ftmuted { color: var(--muted); font-size: 13px; padding: 8px 12px; }
  .ftempty { line-height: 1.5; }

  /* Granted-Folder section (Thread-scoped, ADR 0013): a labelled divider, folder-root
     rows with a mode/missing badge, sitting beneath the Files-space tree. */
  .ftsection { margin: 8px 8px 2px; padding-top: 8px; border-top: 1px solid var(--line); color: var(--muted); font-size: 10.5px; font-weight: 600; text-transform: uppercase; letter-spacing: .04em; }
  .ftfolder.missing { color: var(--muted); }
  .ftfolder.missing .ftcaret { opacity: .4; cursor: default; }
  .ftbadge { flex: none; margin-left: auto; padding: 1px 6px; border-radius: 999px; background: var(--code); color: var(--muted); font-size: 10px; font-family: var(--mono); white-space: nowrap; }
  .ftbadge.rw { background: color-mix(in srgb, var(--accent) 18%, transparent); color: var(--accent); }
  .ftbadge.warn { margin-left: 6px; background: color-mix(in srgb, var(--danger) 16%, transparent); color: var(--danger); }
  .ftcaption { flex: none; margin: 0; padding: 6px 10px; border-top: 1px solid var(--line); color: var(--muted); font-family: var(--mono); font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
