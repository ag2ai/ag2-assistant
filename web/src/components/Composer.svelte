<script lang="ts">
  import { onMount } from 'svelte'
  import { thread, SETTINGS_PAGE, profiles, runInfo } from '../store.ts'
  import { openOverlay } from '../router.ts'
  import { send, stop, startVoice, stopVoice, voice } from '../controller.ts'
  import { liveConfigs, loadLiveConfigs } from '../lib/live.ts'
  import { llmConfigs } from '../lib/llm.ts'
  import { api } from '../transport/api/index.ts'
  import { getActiveProfileId } from '../lib/profile.ts'
  import { chatChips, inheritedCount, addPlan } from '../lib/chatFolders.ts'
  import { makePick, triggerAt, applyPick, composeMessage, highlightSegments } from '../lib/fileRefs.ts'
  import { foldersStore, loadFolders, applyFolders } from '../lib/folders.ts'
  import { errText } from '../lib/errors.ts'
  import { ApiError } from '../transport/http.ts'
  import { FolderConflict } from '../schemas/index.ts'
  import type { AttachmentPayload, Folder, FsRoots, SearchHit } from '../schemas/index.ts'
  import type { FolderChip } from '../lib/chatFolders.ts'
  import type { FileRef } from '../lib/fileRefs.ts'
  import Icon from './Icon.svelte'
  import ModelSwitcher from './composer/ModelSwitcher.svelte'
  import FolderPicker from './FolderPicker.svelte'
  import ChatFolders from './ChatFolders.svelte'

  // The live-voice button needs an ACTIVE live config to run — load the shared store
  // (same one Settings → Live mutates, so adding/activating one enables the button
  // live). No active config → the button reads as muted and routes to Settings → Models
  // (its Live section) instead of starting a session.
  onMount(() => {
    loadLiveConfigs().catch(() => {})
    // Shared Folder snapshot — Settings → Folders and the ChatFolders modal mutate
    // the same store, so their changes reach the chip strip live. Load once.
    if (!$foldersStore.loaded) loadFolders()
    // fs roots for the folder picker — install-wide, so the active profile's
    // settings answer works. Best-effort; the picker degrades to no root shortcuts.
    api.settings().then((s) => { fsRoots = s.fs }).catch(() => {})
  })

  // Per-chat Folder access (CONTEXT.md "Grant"): only the chat-scoped layer shows
  // as removable chips; profile/task reach shows as a "+N inherited folder" note.
  const pid = $derived($profiles.activeId || getActiveProfileId() || '')
  const chatId = $derived($thread.chat)
  // Тред рана несёт таску: её task-scope папки наследуются в этот чат (см. spec
  // 2026-07-20). runInfo грузится опросом — до первого ответа taskId пуст, и
  // страйп временно показывает только профильный/чатовый уровни. Defense-in-depth:
  // openThread resets runInfo on navigation, but only trust it here if it still
  // belongs to the currently open run — a stale value from the previous run must
  // never leak into this thread's folder grants.
  const taskId = $derived($thread.kind === 'run' && $runInfo?.id === $thread.id ? ($runInfo.task_id || '') : '')
  // An empty chatId means PROFILE scope in setGrant, so never expose it without one.
  const showFolders = $derived(($thread.kind === 'chat' || $thread.kind === 'run') && !!chatId)

  const folders = $derived($foldersStore.folders)   // shared snapshot {id,name,path,exists,grants[]}
  let fsRoots = $state<Partial<FsRoots>>({})
  let picking = $state(false)       // FolderPicker modal open
  let foldersModal = $state(false)  // ChatFolders (mode pills) modal open
  let folderBusy = $state(false)
  let folderErr = $state('')
  let folderNote = $state('')

  const chips = $derived(showFolders ? chatChips(folders, pid, chatId, taskId) : [])
  const extraCount = $derived(showFolders ? inheritedCount(folders, pid, chatId, taskId) : 0)

  // Busy/error wrapper for a folder op; leaves `folders` to the op itself.
  async function folderOp(fn: () => Promise<void>) {
    folderErr = ''; folderNote = ''; folderBusy = true
    try { await fn() } catch (e) { folderErr = errText(e) }
    folderBusy = false
  }
  // No per-chat reload: the shared snapshot holds every chat's grants, so the
  // chip strip re-derives (via chatChips/chatId) when you switch chats.

  function openPicker() { folderErr = ''; folderNote = ''; picking = true }

  // Pick a path → mint (or reuse on 409) the Folder → grant this chat read, unless
  // a profile Grant already covers it (then just say so).
  const addFolder = (path: string) => folderOp(async () => {
    let snap: { folders: Folder[] }
    let folder: Folder | undefined
    try {
      snap = await api.createFolder(path)
      folder = snap.folders.find((f) => f.path === path)
    } catch (e) {
      // 409 = the path is already registered; the body points at that Folder.
      const clash = e instanceof ApiError && e.status === 409 ? FolderConflict.safeParse(e.body) : null
      if (clash?.success) {
        snap = await api.folders()
        folder = snap.folders.find((f) => f.id === clash.data.existing.id)
      } else throw e
    }
    if (!folder) throw new Error('Could not resolve that folder')
    const plan = addPlan(folder, pid, chatId, taskId)
    if (plan.status === 'grant') snap = await api.setGrant(folder.id, pid, 'read', chatId)
    else if (plan.status === 'unblock') { snap = await api.revokeGrant(folder.id, pid, chatId); folderNote = `"${plan.name}" is available here again.` }
    else if (plan.status === 'covered') folderNote = `"${plan.name}" is already available here.`
    else if (plan.status === 'exists') folderNote = `"${folder.name}" is already in this chat.`
    applyFolders(snap)
    picking = false
  })

  const removeChip = (f: FolderChip) => folderOp(async () => {
    applyFolders(await api.revokeGrant(f.id, pid, chatId))
  })

  // The modal mutates the same shared store, so the strip is already in sync.
  function closeFoldersModal() { foldersModal = false }
  const noLiveModel = $derived(!$liveConfigs.active)

  // No Text/LLM model configured → sending is pointless (it would fail with no model /
  // key), so gate the Send button. Mirror ModelSwitcher's "No models configured" state:
  // no configs AND no env pin. Gate on `loaded` so a not-yet-fetched store doesn't
  // flash Send disabled. The shared llmConfigs store is loaded by ModelSwitcher.
  const noTextModel = $derived(
    $llmConfigs.loaded && !$llmConfigs.configs.length && !$llmConfigs.envOverride
  )

  let text = $state('')
  let pending = $state<{ name: string; payload: AttachmentPayload }[]>([])
  let dragging = $state(false)  // an OS-file drag is hovering the composer
  let ta: HTMLTextAreaElement | undefined
  let fileInput: HTMLInputElement | undefined
  let hl: HTMLDivElement | undefined

  function syncScroll() { if (hl && ta) { hl.scrollTop = ta.scrollTop; hl.scrollLeft = ta.scrollLeft } }

  // ---- `@` File references (ADR 0012): an ordered picks list (source of truth),
  // a type-to-filter picker over /files/search, and a `Referenced files:` block
  // appended on send. The inline `@label` is cosmetic; deleting it drops the pick
  // (reconciled in composeMessage). Structurally parallel to `pending`. ----
  let refs = $state<FileRef[]>([])          // ordered File-reference picks
  let atOpen = $state(false)                // picker visible
  let atQuery = $state('')                  // the text typed after the active `@`
  let atStart = $state(-1)                  // index of the active `@` in `text`
  let atResults = $state<SearchHit[]>([])   // current search results
  let atIndex = $state(0)                   // highlighted result
  let atSeq = 0                             // debounce + stale-response guard
  let atTimer: ReturnType<typeof setTimeout> | undefined
  let atList = $state<HTMLDivElement | undefined>()   // the picker's scroll container
  let atRows: HTMLButtonElement[] = []      // per-row elements, for scroll-into-view on nav

  // Cosmetic highlight backdrop: a mirror of the text sitting behind the (transparent)
  // textarea, with each surviving `@label` wrapped in a <mark>. Metrics match .cinput
  // exactly so the marks land under their labels; scroll is kept in lockstep by syncScroll.
  const hlSegs = $derived(highlightSegments(text, refs))

  // Keep the highlighted row visible as the selection moves — `nearest` only scrolls
  // when the row is actually out of view, so an in-view move never jumps the list.
  function atScroll() { atRows[atIndex]?.scrollIntoView({ block: 'nearest' }) }
  // Rows that fit the visible list, so PageUp/PageDown jump a screenful.
  function atPage() {
    const rh = atRows[0]?.offsetHeight || 32
    return Math.max(1, Math.floor((atList?.clientHeight || rh) / rh))
  }

  function closeAt() { atOpen = false; atQuery = ''; atStart = -1; atResults = []; atIndex = 0; atSeq++ }

  // Reflect the caret into the `@`-trigger: open+filter when the caret sits in a
  // fresh `@token`, close otherwise. Runs on input and on caret moves (keyup/click).
  function syncAt() {
    if (!ta) return
    const trig = triggerAt(text, ta.selectionStart)
    if (!trig) { if (atOpen) closeAt(); return }
    // Only re-search when the active `@token` actually changed. Arrow-key navigation
    // fires keyup with the caret pinned (keydown preventDefault'd it), so without this
    // guard every arrow press would refire the search and reset the highlight to row 0.
    const changed = !atOpen || trig.start !== atStart || trig.query !== atQuery
    atStart = trig.start; atQuery = trig.query; atOpen = true
    if (changed) runAtSearch(trig.query)
  }
  function runAtSearch(q: string) {
    clearTimeout(atTimer)
    const seq = ++atSeq
    atTimer = setTimeout(async () => {
      try {
        const res = await api.searchFiles(q, chatId)
        if (seq !== atSeq) return           // a newer keystroke won
        atResults = res.results; atIndex = 0
      } catch { if (seq === atSeq) atResults = [] }
    }, 120)
  }
  function choosePick(result: SearchHit | undefined) {
    if (!result) return
    const caret = ta ? ta.selectionStart : text.length
    const p = makePick(result)
    const next = applyPick(text, atStart, caret, p.label)
    text = next.text
    refs = [...refs, p]
    closeAt()
    // Restore the caret past the inserted label once Svelte flushes the bound value.
    queueMicrotask(() => { if (ta) { ta.focus(); ta.selectionStart = ta.selectionEnd = next.caret; grow() } })
  }

  function submit() {
    if (!text.trim() && !pending.length) return
    // Append the reconciled `Referenced files:` block to the text the user wrote —
    // no change to send()'s (text, attachments) contract (the block rides in text).
    const outText = composeMessage(text, refs).trim()
    if (!outText && !pending.length) return
    send(outText, pending.map((p) => p.payload))
    text = ''; pending = []; refs = []; closeAt()
    if (ta) ta.style.height = 'auto'
  }
  function key(e: KeyboardEvent) {
    // The picker owns navigation keys while it's open over a non-empty result set.
    if (atOpen && atResults.length) {
      const last = atResults.length - 1
      const jump = (i: number) => { e.preventDefault(); atIndex = Math.max(0, Math.min(last, i)); atScroll() }
      if (e.key === 'ArrowDown') { e.preventDefault(); atIndex = (atIndex + 1) % atResults.length; atScroll(); return }
      if (e.key === 'ArrowUp') { e.preventDefault(); atIndex = (atIndex - 1 + atResults.length) % atResults.length; atScroll(); return }
      if (e.key === 'PageDown') { jump(atIndex + atPage()); return }
      if (e.key === 'PageUp') { jump(atIndex - atPage()); return }
      if (e.key === 'Home') { jump(0); return }
      if (e.key === 'End') { jump(last); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); choosePick(atResults[atIndex]); return }
    }
    if (atOpen && e.key === 'Escape') { e.preventDefault(); closeAt(); return }
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
  }
  // While the agent is working, Enter still sends — the message is fed to the running
  // turn (it picks it up at its next step), so say so rather than leaving it a mystery.
  function placeholder() {
    if ($thread.busy) return 'Add something while it works…'
    return $thread.kind === 'task' ? 'Tell the agent to change this task…' : 'Message AG2 Assistant…'
  }
  function grow() { if (ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px' } }
  // A running session always toggles off. Otherwise: no active Live model → route to
  // Settings → Models to configure one; else start the session.
  function liveClick() {
    if (!$voice.active && noLiveModel) {
      openOverlay('settings', SETTINGS_PAGE.MODELS)
      return
    }
    $voice.active ? stopVoice() : startVoice()
  }

  function toB64(file: File): Promise<string> {
    return new Promise((res, rej) => {
      const r = new FileReader()
      r.onload = () => res(String(r.result).split(',')[1] || '')
      r.onerror = rej
      r.readAsDataURL(file)
    })
  }

  // Give a nameless clipboard/dropped file a MIME-derived extension so the chip
  // reads sensibly and the backend routes it by extension.
  const MIME_EXT: Record<string, string | undefined> = {
    'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp',
    'application/pdf': 'pdf', 'audio/mpeg': 'mp3', 'audio/ogg': 'ogg', 'video/mp4': 'mp4',
    'video/webm': 'webm', 'text/plain': 'txt',
  }
  let pasteSeq = 0  // per-load counter so multiple pasted screenshots don't collide
  function extFor(mime: string) {
    const known = MIME_EXT[mime]
    if (known) return known
    const sub = String(mime || '').split('/')[1] || ''
    return sub.replace(/[^a-z0-9].*$/i, '') || 'bin'  // "image/svg+xml" -> "svg"
  }
  function nameFor(f: File) {
    if (f.name && /\.[^.]+$/.test(f.name)) return f.name  // real file: has an extension
    return `pasted-${++pasteSeq}.${extFor(f.type)}`
  }

  // Shared attachment pipeline for every entry point (picker, paste, drop): encode
  // each file and add it to the pending row as a transient message Attachment.
  async function addFiles(files: Iterable<File>) {
    for (const f of files) {
      const data = await toB64(f)
      const name = nameFor(f)
      pending = [...pending, { name, payload: { name, mime: f.type, data } }]
    }
  }

  async function pick(e: Event & { currentTarget: HTMLInputElement }) {
    await addFiles(e.currentTarget.files || [])
    if (fileInput) fileInput.value = ''
  }

  // Clipboard with ≥1 file → attachment paste (suppress the text rep); no file →
  // let the normal text paste run.
  function paste(e: ClipboardEvent) {
    const files = [...(e.clipboardData?.items || [])]
      .filter((it) => it.kind === 'file')
      .map((it) => it.getAsFile())
      .filter((f): f is File => f !== null)
    if (!files.length) return
    e.preventDefault()
    addFiles(files)
  }

  // Drop OS files onto the composer → message Attachment (FilesTree's drop uploads
  // to the workspace instead). Internal row drags carry no files and are ignored.
  function dragover(e: DragEvent) {
    if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); dragging = true }
  }
  function dragleave(e: DragEvent & { currentTarget: HTMLElement }) {
    const to = e.relatedTarget
    if (!(to instanceof Node) || !e.currentTarget.contains(to)) dragging = false
  }
  function drop(e: DragEvent) {
    const files = e.dataTransfer?.files
    if (!files || !files.length) return
    e.preventDefault()
    dragging = false
    addFiles(files)
  }
  const removeFile = (i: number) => { pending = pending.filter((_, j) => j !== i) }
</script>

<div class="composer">
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="inputbox" class:busy={$thread.busy} class:dragging
       ondragover={dragover} ondragleave={dragleave} ondrop={drop}>
    {#if showFolders && (extraCount || chips.length)}
      <div class="cfolders">
        {#if extraCount}
          <button class="cfmore" title="Folder access inherited here" onclick={() => (foldersModal = true)}>+{extraCount} inherited folder{extraCount === 1 ? '' : 's'}</button>
        {/if}
        {#each chips as f (f.id)}
          <span class="chip folder" class:missing={!f.exists}>
            <button class="chipbody" title={`${f.path} · ${f.mode === 'read_write' ? 'read + write' : 'read'}`} onclick={() => (foldersModal = true)}>
              <Icon name="folder" size={13} /> {f.name}
            </button>
            {#if f.mode === 'read_write'}
              <span class="cfrw" title="read + write">R+W</span>
            {/if}
            <button class="x" title="Remove from this chat" aria-label="Remove folder from this chat" disabled={folderBusy} onclick={() => removeChip(f)}>×</button>
          </span>
        {/each}
      </div>
    {/if}
    {#if showFolders && (folderNote || folderErr)}
      <div class="cfmsg" class:err={!!folderErr}>{folderErr || folderNote}</div>
    {/if}
    {#if pending.length}
      <div class="pending">
        {#each pending as p, i}
          <span class="chip"><Icon name="paperclip" size={13} /> {p.name}<button class="x" onclick={() => removeFile(i)}>×</button></span>
        {/each}
      </div>
    {/if}
    <input type="file" multiple hidden bind:this={fileInput} onchange={pick} />
    {#if atOpen}
      <!-- `@` File-reference picker (ADR 0012): type-to-filter over reachable files.
           mousedown+preventDefault keeps textarea focus so the pick lands before blur. -->
      <div class="atpicker" role="listbox" aria-label="Reference a file" bind:this={atList}>
        {#if atResults.length}
          {#each atResults as r, i (r.path)}
            <button type="button" class="atrow" class:sel={i === atIndex} role="option" aria-selected={i === atIndex}
                    bind:this={atRows[i]}
                    onmousedown={(e) => { e.preventDefault(); choosePick(r) }}
                    onmouseenter={() => (atIndex = i)}>
              <Icon name={r.kind === 'directory' ? 'folder' : 'file-text'} size={14} />
              <span class="atname">{r.name}</span>
              {#if r.dir}<span class="atdir">{r.dir}</span>{/if}
            </button>
          {/each}
        {:else}
          <div class="atempty">{atQuery ? 'No matching files' : 'Type to search files…'}</div>
        {/if}
      </div>
    {/if}
    <div class="cinput-wrap">
      <!-- Highlight backdrop: mirrors the text so each `@label` shows a subtle mark
           behind the real (transparent) textarea glyphs. aria-hidden + pointer-events:
           none — it's purely decorative; the textarea keeps focus, caret and a11y. -->
      <div class="cinput-hl" bind:this={hl} aria-hidden="true">{#each hlSegs as s}{#if s.mark}<mark>{s.text}</mark>{:else}{s.text}{/if}{/each}</div>
      <textarea
        class="cinput"
        bind:this={ta}
        bind:value={text}
        rows="1"
        placeholder={placeholder()}
        oninput={() => { grow(); syncAt(); syncScroll() }}
        onkeydown={key}
        onkeyup={syncAt}
        onclick={syncAt}
        onscroll={syncScroll}
        onblur={() => closeAt()}
        onpaste={paste}
      ></textarea>
    </div>
    <div class="cbar">
      <button class="cbtn" onclick={() => fileInput?.click()} title="Attach files" aria-label="Attach files"><Icon name="plus" size={18} /></button>
      {#if showFolders}
        <!-- Add a Folder to this chat: a persistent read Grant, not a transient
             message attachment like the + above. -->
        <button class="cbtn" onclick={openPicker} title="Add a folder to this chat" aria-label="Add a folder to this chat"><Icon name="folder-plus" size={18} /></button>
      {/if}
      <div class="cbar-right">
        <ModelSwitcher />
        <!-- Single live-voice control: toggles the realtime voice session. Disabled
             until a Live model is active (unless a session is already running, so it
             can still be stopped). The wrapper carries the explain-why tooltip because a
             disabled button doesn't fire hover on its own. -->
        <span class="ctip">
          <button class="cbtn live" class:on={$voice.active} class:needcfg={noLiveModel && !$voice.active}
                  onclick={liveClick}
                  title={noLiveModel ? undefined : 'Live voice'}
                  aria-label={noLiveModel ? 'Configure a Live model' : 'Live voice'}><Icon name="waveform" size={18} /></button>
          {#if noLiveModel && !$voice.active}
            <span class="ctip-bubble" role="tooltip">No Live model yet — click to configure one in Settings and enable Live support.</span>
          {/if}
        </span>
        <!-- Primary action. While a turn runs it's Stop; Enter still sends (feeds the
             running turn), so "add while it works" survives via the keyboard. Idle it's
             Send, disabled until there's text or an attachment. -->
        {#if $thread.busy}
          <button class="csend stop" onclick={stop} title="Stop the agent" aria-label="Stop the agent"><Icon name="square" size={15} /></button>
        {:else}
          <span class="ctip">
            <button class="csend" onclick={submit}
                    disabled={noTextModel || (!text.trim() && !pending.length)}
                    title={noTextModel ? undefined : 'Send'} aria-label="Send"><Icon name="arrow-up" size={18} /></button>
            {#if noTextModel}
              <span class="ctip-bubble" role="tooltip">No model configured — add one in Settings → Models to send messages.</span>
            {/if}
          </span>
        {/if}
      </div>
    </div>
  </div>
  <div class="cnote">AG2 Assistant is AI and can make mistakes. Check important info.</div>
</div>

{#if picking}
  <!-- Backdrop: click-to-dismiss duplicates the Cancel button, so it stays out of
       the a11y tree rather than becoming a second focusable control. -->
  <div class="modal-backdrop over" role="presentation" onclick={() => (picking = false)}></div>
  <div class="modal over">
    <h2>Add a folder to this chat</h2>
    <p class="muted cfhint">Gives this conversation <b>read</b> access to a folder outside the workspace. Change the mode or remove it anytime from the chip.</p>
    {#if folderErr}<p class="cfmsg err">{folderErr}</p>{/if}
    <FolderPicker roots={fsRoots} start={fsRoots.cwd || fsRoots.home || ''} busy={folderBusy} onUse={addFolder} />
    <button class="modal-close" onclick={() => (picking = false)}>Cancel</button>
  </div>
{/if}
{#if foldersModal}
  <ChatFolders {chatId} {taskId} onClose={closeFoldersModal} />
{/if}

<style>
  /* Dragging an OS file over the composer highlights it as a drop target for a
     transient message attachment (mirrors the focus ring). */
  .inputbox.dragging { border-color: var(--accent-border); box-shadow: var(--focus-ring), var(--shadow-md); }
  /* Anchor the floating `@`-picker to the composer box. */
  .inputbox { position: relative; }

  /* Highlight backdrop for `@` File references. The backdrop renders ALL the visible
     glyphs (plain text in --ink, each `@label` in the accent colour over a tinted fill);
     the textarea on top is made fully transparent, contributing only its caret and
     selection. Every metric here MUST match the global .cinput rule (app.css) or the
     text drifts off the caret. */
  .cinput-wrap { position: relative; }
  .cinput-hl {
    position: absolute; inset: 0; z-index: 0;
    margin: 0; padding: 8px 4px 6px;
    font: inherit; font-size: var(--text-md); color: var(--ink);
    white-space: pre-wrap; overflow-wrap: break-word; word-break: break-word;
    overflow: hidden; pointer-events: none; user-select: none;
  }
  /* No padding on the mark — horizontal/vertical padding would shift glyph metrics
     away from the textarea. A tinted, rounded fill (radius is layout-neutral) plus the
     accent ink is enough; box-decoration-break keeps the ends rounded across a wrap. */
  .cinput-hl mark {
    background: var(--accent-soft); color: var(--accent); border-radius: 4px; padding: 0;
    -webkit-box-decoration-break: clone; box-decoration-break: clone;
  }
  /* The textarea sits over the backdrop with transparent glyphs (the backdrop draws
     them) and a transparent bg (the marks show through); keep the caret + placeholder. */
  .cinput { position: relative; z-index: 1; background: transparent; color: transparent; -webkit-text-fill-color: transparent; caret-color: var(--ink); }
  .cinput::placeholder { color: var(--text-muted); -webkit-text-fill-color: var(--text-muted); }

  /* `@` File-reference picker — a floating result list above the textarea. Bounded
     height so a large corpus scrolls rather than growing the composer. */
  .atpicker {
    position: absolute; bottom: calc(100% + 6px); left: 0; right: 0;
    max-height: 240px; overflow-y: auto; z-index: 45;
    background: var(--surface-elevated); border: 1px solid var(--line);
    border-radius: var(--radius-sm); box-shadow: var(--shadow-lg); padding: 4px;
  }
  .atrow {
    display: flex; align-items: center; gap: 8px; width: 100%; text-align: left;
    background: none; border: none; border-radius: var(--radius-sm);
    padding: 6px 8px; font: inherit; color: var(--ink); cursor: pointer;
  }
  .atrow.sel { background: var(--surface-sunken, rgba(127, 127, 127, .14)); }
  .atname { flex: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 60%; }
  .atdir {
    flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    text-align: right; font-size: 12px; color: var(--text-muted);
  }
  .atempty { padding: 8px 10px; font-size: 13px; color: var(--text-muted); }

  /* Hover-explain tooltip for the composer's gated buttons (Live mic, Send). Anchored to
     the button's right edge (both live at the right of the composer) so the bubble never
     runs off-screen. The wrapper carries the hover because a disabled button can't. */
  .ctip { position: relative; display: inline-flex; }
  /* Muted (no Live model yet) but still clickable — it routes to Settings, so it
     stays a pointer, not a dead disabled control. */
  .cbtn.needcfg { opacity: .45; }
  .ctip-bubble {
    position: absolute; bottom: calc(100% + 8px); right: 0;
    width: max-content; max-width: 220px; text-align: left; line-height: 1.35;
    background: var(--surface-elevated); color: var(--ink);
    border: 1px solid var(--line); border-radius: var(--radius-sm);
    box-shadow: var(--shadow-lg); padding: 7px 10px; font-size: 12px;
    opacity: 0; pointer-events: none; transform: translateY(3px);
    transition: opacity 120ms ease, transform 120ms ease; z-index: 40;
  }
  .ctip:hover .ctip-bubble { opacity: 1; transform: translateY(0); }

  /* Persistent per-chat Folder chips — sit above the transient attachment row and
     survive send/reload (they're Grants, not message payload). */
  .cfolders { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 6px; }
  .chip.folder { position: relative; display: inline-flex; align-items: center; transition: padding-right var(--dur-fast) var(--ease-out); }
  .chip.folder .chipbody {
    display: inline-flex; align-items: center; gap: 5px; background: none; border: none;
    padding: 0; margin: 0; font: inherit; color: inherit; cursor: pointer; max-width: 220px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  /* Read is the default (no marker). Read+write gets a borderless warn-colored
     "R+W" label — the extra capability is the thing worth flagging. */
  .chip.folder .cfrw { flex: none; font-size: 9px; font-weight: 700; letter-spacing: .04em; line-height: 1; color: var(--warning, #b8860b); }
  .chip.folder.missing .chipbody { color: var(--warning, #b8860b); }
  /* Remove control is revealed on hover (or keyboard focus) as a bare icon.
     Absolutely placed so revealing it extends the chip rightward, never its height. */
  .chip.folder .x {
    position: absolute; right: 7px; top: 50%; transform: translateY(-50%);
    display: none; align-items: center; justify-content: center; padding: 0;
    border: none; background: none; color: var(--muted); cursor: pointer;
    font-size: 15px; line-height: 1;
  }
  .chip.folder:hover, .chip.folder:focus-within { padding-right: 22px; }
  .chip.folder:hover .x, .chip.folder:focus-within .x { display: inline-flex; }
  .chip.folder .x:hover { color: var(--ink); }
  .chip.folder .x:disabled { opacity: .5; cursor: default; }
  .cfmore {
    background: none; border: 1px dashed var(--line); border-radius: var(--radius-pill, 999px);
    padding: 2px 10px; font-size: 12px; color: var(--text-muted); cursor: pointer;
  }
  .cfmore:hover { color: var(--text); border-color: var(--text-muted); }
  .cfmsg { font-size: 12px; color: var(--text-muted); margin: 0 0 6px; }
  .cfmsg.err { color: var(--danger); }
  .cfhint { font-size: 12px; margin: 0 0 10px; }
</style>
