<script lang="ts">
  // The docked preview rail: renders the route's `aside` file (or the path-less
  // transient `viewer` body) beside the conversation in the grid's right column.
  // html/image/pdf render natively; md/code/text in-app; unknown types → download.
  import { onMount, onDestroy } from 'svelte'
  import { route, closeAside } from '../router.ts'
  import { viewer, previewWidth, previewExpanded, resetPreviewView, revealFile, openThreadRow } from '../store.ts'
  import { threadScope } from '../lib/threadScope.ts'
  import { api } from '../transport/api/index.ts'
  import Markdown from './Markdown.svelte'
  import RailResizer from './RailResizer.svelte'
  import Icon from './Icon.svelte'
  import { viewerKind, mentionsLabel, mentionRowTitle, mentionRowIcon } from '../lib/preview.ts'
  import { saveErrorMessage, isConflict } from '../lib/fileEdit.ts'
  import { setUnsavedGuard } from '../lib/unsavedGuard.ts'
  import { isFolderPath, folderAffordances } from '../lib/folderFiles.ts'
  import { errText } from '../lib/errors.ts'
  import type { MentionRow } from '../schemas/index.ts'
  import { m } from '../paraglide/messages.js'

  // A URL-addressed file wins; a path-less transient body is the fallback when no
  // file is addressed. The rail shows exactly one of them.
  const file = $derived($route.aside?.kind === 'file' ? $route.aside : null)
  const transient = $derived(!file && $viewer?.text != null ? $viewer : null)

  const path = $derived(file?.path || null)
  const name = $derived(path ? path.split('/').pop() : null)
  const title = $derived(file ? (name || m.viewer_preview()) : (transient?.title || m.viewer_preview()))
  const kind = $derived(path ? viewerKind(name) : 'markdown')
  // The open Thread's scope token (lib/threadScope.js) scopes a Folder (absolute) file's
  // Grant resolution (ADR 0013); a Files-space (relative) path ignores it, per rawQuery.
  const chatId = $derived($threadScope)
  const url = $derived(path ? api.fileUrl(path, false, chatId) : '')
  const downloadUrl = $derived(path ? api.fileUrl(path, true, chatId) : '')
  const native = $derived(kind === 'html' || kind === 'pdf' || kind === 'image')
  // Offer a copy button for text-backed kinds and html (copy the source), and for
  // images (copy the pixels). markdown additionally gets a Preview/Edit switcher.
  const copyable = $derived(kind === 'markdown' || kind === 'code' || kind === 'text' || kind === 'html' || kind === 'image')
  // The served file's resolved Grant mode (`X-File-Mode`), captured from the markdown
  // load: null until it arrives, then read | read_write. A Files-space file reads back
  // read_write; a Folder file carries its Thread-scoped Grant mode (ticket 04).
  let fileMode = $state<string | null>(null)
  // In-place editing (ADR 0011) is offered for path-backed markdown only: the
  // transient body has nowhere to save, and other kinds don't opt in yet. A Folder
  // (absolute) file additionally needs a read_write Grant — a read-only Folder file
  // is preview + download only (the server would 403 a write anyway; ticket 04).
  const editable = $derived(
    kind === 'markdown' && !!path && (isFolderPath(path) ? folderAffordances(fileMode).edit : true)
  )

  // Close strips the aside key from the URL for a file; clears the store for a
  // transient body (it was never in the URL).
  function close() {
    if (file) closeAside()
    else $viewer = null
  }

  // Unmounting (any close path) forgets the preview's width + expanded; the next open
  // starts docked. A reload skips onDestroy, so a file preview survives it (App boot reconciles).
  onDestroy(resetPreviewView)

  // `text` is the last-loaded/last-saved baseline (render + dirty compare); `draft` is
  // the editor's working copy (also what Preview renders); `etag` its version token.
  let text = $state('')
  let draft = $state('')
  let etag = $state<string | null>(null)
  let err = $state('')
  let mode = $state('preview')   // markdown only: 'preview' render vs. 'edit' source
  // The unknown/"download" kind gets an on-demand raw view: 'preview' shows the
  // download message, 'raw' shows the bytes as text — most extensionless/dotfile
  // "unknown" files (.skillignore, Dockerfile, .env) are really text. The bytes are
  // fetched lazily on the first switch (once per file) so a genuine binary that the
  // user only means to download is never pulled down needlessly.
  let dlView = $state('preview')
  let rawText = $state('')
  let rawErr = $state('')
  let rawLoaded = $state(false)
  let saving = $state(false)
  let saveErr = $state('')
  // A save clashed with a since-changed file: the rail shows a Reload/Overwrite
  // choice (ADR 0011) instead of a plain error and keeps the draft until they pick.
  let conflict = $state(false)
  // An image whose source failed to load (missing/unreadable file): we draw the
  // path instead of letting the browser show its default broken-image glyph.
  let imgErr = $state(false)
  // Dirty as soon as the working copy diverges from the baseline (edit-capable kinds
  // only); gates the unsaved marker and the Save affordance.
  const dirty = $derived(editable && draft !== text)
  // Expose the editor's dirty state to the router's aside guard while mounted.
  onMount(() => setUnsavedGuard(() => dirty))
  onDestroy(() => setUnsavedGuard(null))

  // "Mentioned in N threads" backlink (ADR 0014): the Threads whose transcript
  // mentions THIS file. Loaded on-demand for path-backed previews only (the transient
  // body has no file to trace); the header affordance self-hides when the list is
  // empty. `mentionsOpen` toggles the anchored popover.
  let mentions = $state<MentionRow[]>([])
  let mentionsOpen = $state(false)
  $effect(() => {
    const p = path, cid = chatId
    mentions = []; mentionsOpen = false   // a newly-opened file starts with a closed, unloaded list
    if (!p) return                        // transient/path-less body: nothing to trace
    let stale = false                     // a late load for a since-changed file must not land
    api.fileMentions(p, cid)
      .then((r) => { if (!stale) mentions = r.threads })
      .catch(() => { if (!stale) mentions = [] })   // best-effort backlink: a failure just hides it
    return () => { stale = true }
  })
  function toggleMentions() { mentionsOpen = !mentionsOpen }
  function openMention(row: MentionRow) { openThreadRow(row); mentionsOpen = false }  // navigate; keep the preview open
  // Dismiss the popover on outside-click / Esc, mirroring FilesTree's menu.
  function onDocPointer(e: PointerEvent) {
    const t = e.target instanceof Element ? e.target : null
    if (mentionsOpen && !t?.closest('.vmentions-pop') && !t?.closest('.vmentions')) mentionsOpen = false
  }
  function onDocKey(e: KeyboardEvent) { if (e.key === 'Escape') mentionsOpen = false }
  onMount(() => {
    document.addEventListener('pointerdown', onDocPointer, true)
    document.addEventListener('keydown', onDocKey)
    return () => {
      document.removeEventListener('pointerdown', onDocPointer, true)
      document.removeEventListener('keydown', onDocKey)
    }
  })

  $effect(() => {
    const p = path, tr = transient, k = kind, cid = chatId
    let stale = false            // a late load for a since-changed file must not land
    text = ''; draft = ''; etag = null
    err = ''; saveErr = ''; conflict = false; saving = false
    imgErr = false               // a fresh source gets a fresh chance to load
    mode = 'preview'             // each newly-opened file starts on the rendered view
    dlView = 'preview'; rawText = ''; rawErr = ''; rawLoaded = false  // unknown-kind raw view resets per file
    fileMode = null              // re-resolve the Grant mode for the newly-opened file
    if (tr) { text = tr.text; draft = tr.text }
    else if (p && k === 'markdown') {
      api.fileTextWithEtag(p, cid)
        .then(({ text: t, etag: e, mode: m }) => { if (!stale) { text = t; draft = t; etag = e; fileMode = m } })
        .catch((e) => { if (!stale) err = errText(e) })
    } else if (p && (k === 'code' || k === 'text')) {
      api.fileText(p, cid)
        .then((t) => { if (!stale) { text = t; draft = t } })
        .catch((e) => { if (!stale) err = errText(e) })
    }
    return () => { stale = true }
  })

  // Switch the unknown-kind view to raw and lazily fetch the bytes as text on the
  // first switch. A mid-flight file switch drops the result (guarded on `path`);
  // rawLoaded gates against refetching (an empty file legitimately reads back '').
  async function showRaw() {
    dlView = 'raw'
    if (rawLoaded) return
    rawLoaded = true
    const p = path, cid = chatId
    if (!p) return
    try {
      const t = await api.fileText(p, cid)
      if (p === path) rawText = t
    } catch (e) {
      if (p === path) rawErr = errText(e)
    }
  }

  // Copy to the clipboard, with a brief ✓ confirmation: text-backed kinds copy the
  // source (the live editor draft for markdown); images copy the pixels.
  let copied = $state(false)
  async function copy() {
    try {
      if (kind === 'image') {
        // The clipboard only accepts PNG across browsers, so hand ClipboardItem a
        // promise that fetches + rasterises the image — resolving it inside write()
        // keeps the call in the user-gesture tick (Safari requires that).
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': imagePng() })])
      } else if (kind === 'html') {
        // html renders natively (iframe), so its source isn't held in `draft` — fetch it.
        await navigator.clipboard.writeText(path ? await api.fileText(path, chatId) : '')
      } else {
        await navigator.clipboard.writeText(draft || '')
      }
      copied = true
      setTimeout(() => (copied = false), 1400)
    } catch { /* clipboard blocked / unsupported — no-op */ }
  }

  // Write the draft against `baseTag` (the open-time etag, or null to force past a
  // conflict); on success adopt it + the returned etag, a `409` raises the prompt.
  async function persist(baseTag: string | null) {
    if (saving) return
    const p = path               // the file this write targets; a mid-flight switch drops the result
    if (!p) return
    saving = true
    saveErr = ''
    const pending = draft
    try {
      const newTag = await api.saveFile(p, pending, baseTag, chatId)
      if (p !== path) return
      text = pending
      etag = newTag
      conflict = false
    } catch (e) {
      if (p !== path) return
      if (isConflict(e)) conflict = true
      else { saveErr = saveErrorMessage(e); conflict = false }
    } finally {
      if (p === path) saving = false
    }
  }

  // Explicit Save (button / ⌘S): only when there's something to write and no conflict
  // is pending (that's Reload/Overwrite's job); checks the open-time token so a
  // concurrent change becomes a conflict rather than a blind clobber.
  function save() {
    if (!editable || !dirty || conflict) return
    persist(etag)
  }

  // Overwrite (conflict resolution): re-issue the write with no `If-Match`, replacing
  // the disk version with the draft and adopting the fresh etag — stays in Edit.
  function overwrite() {
    persist(null)
  }

  // Reload (conflict resolution): discard the draft, load the current disk source and
  // its fresh etag as the new baseline, and clear the conflict/dirty state.
  async function reloadFromDisk() {
    if (saving) return
    const p = path               // a mid-flight switch drops the reloaded content
    if (!p) return
    saving = true
    saveErr = ''
    try {
      const { text: t, etag: e } = await api.fileTextWithEtag(p, chatId)
      if (p !== path) return
      text = t
      draft = t
      etag = e
      conflict = false
    } catch (e) {
      if (p !== path) return
      // The disk read failed (e.g. the file was deleted): surface the error alone,
      // not alongside a now-moot conflict chooser.
      saveErr = saveErrorMessage(e)
      conflict = false
    } finally {
      if (p === path) saving = false
    }
  }

  // ⌘/Ctrl-S saves from anywhere while a markdown file is open in the rail.
  function onKeydown(e: KeyboardEvent) {
    if (!editable) return
    if ((e.metaKey || e.ctrlKey) && (e.key === 's' || e.key === 'S')) {
      e.preventDefault()
      save()
    }
  }

  // Refresh / tab-close with unsaved edits triggers the browser's native leave prompt.
  function onBeforeUnload(e: BeforeUnloadEvent) {
    if (!dirty) return
    e.preventDefault()
    e.returnValue = ''
  }

  // Draw the current image onto a canvas and export a PNG blob. The image is
  // same-origin (/api/files/raw), so the canvas stays untainted and exportable.
  async function imagePng(): Promise<Blob> {
    const img = new Image()
    img.src = url
    await img.decode()
    const canvas = document.createElement('canvas')
    canvas.width = img.naturalWidth
    canvas.height = img.naturalHeight
    const ctx = canvas.getContext('2d')
    if (!ctx) throw new Error('canvas 2d context unavailable')
    ctx.drawImage(img, 0, 0)
    return await new Promise((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('canvas export failed'))), 'image/png'))
  }
</script>

<svelte:window onkeydown={onKeydown} onbeforeunload={onBeforeUnload} />

<!-- Shared "file is gone" panel: any kind whose bytes fail to load draws the path
     instead of a broken glyph / raw stack trace. `detail` carries the technical error. -->
{#snippet missing(icon: string, msg: string, detail?: string)}
  <div class="vmissing" role="alert">
    <Icon name={icon} size={28} />
    <p class="vmissing-msg">{msg}</p>
    <code class="vmissing-path">{path}</code>
    {#if detail}<p class="vmissing-detail">{detail}</p>{/if}
  </div>
{/snippet}

<aside class="rail viewer">
  <RailResizer width={previewWidth} onGrab={() => previewExpanded.set(false)} />
  <div class="vhead">
    {#if editable}
      <div class="vseg" role="group" aria-label={m.viewer_view_mode_aria()}>
        <button class="vsegbtn" class:on={mode === 'preview'} aria-pressed={mode === 'preview'}
                title={m.viewer_preview()} aria-label={m.viewer_preview()} onclick={() => (mode = 'preview')}>
          <Icon name="eye" size={14} />
        </button>
        <button class="vsegbtn" class:on={mode === 'edit'} aria-pressed={mode === 'edit'}
                title={m.action_edit_short()} aria-label={m.action_edit_short()} onclick={() => (mode = 'edit')}>
          <Icon name="pencil" size={14} />
        </button>
      </div>
    {:else if kind === 'download'}
      <!-- Unknown type: let the user peek at the bytes as text instead of only
           offering a download (many "unknown" files are really text). -->
      <div class="vseg" role="group" aria-label={m.viewer_view_mode_aria()}>
        <button class="vsegbtn" class:on={dlView === 'preview'} aria-pressed={dlView === 'preview'}
                title={m.viewer_preview()} aria-label={m.viewer_preview()} onclick={() => (dlView = 'preview')}>
          <Icon name="eye" size={14} />
        </button>
        <button class="vsegbtn" class:on={dlView === 'raw'} aria-pressed={dlView === 'raw'}
                title={m.viewer_raw()} aria-label={m.viewer_raw()} onclick={showRaw}>
          <Icon name="file-text" size={14} />
        </button>
      </div>
    {/if}
    {#if path}
      <!-- A path-backed preview: the filename Reveals the file in the Files tree
           (switch Tab, expand its Directories, scroll it into view). -->
      <button class="vtitle" title={m.viewer_reveal_title({ name: name ?? '' })} onclick={() => revealFile(path)}>{title}</button>
    {:else}
      <!-- Path-less transient body: no tree row to reveal, so a plain heading. -->
      <h2 title={title}>{title}</h2>
    {/if}
    {#if path && mentions.length > 0}
      <!-- The reverse link: Threads (Chats + Task Runs) whose transcript mentions this
           file. Self-hiding at zero; a run row stays previewed when opened (ADR 0014). -->
      <div class="vmentions-wrap">
        <button class="vmentions" class:on={mentionsOpen} aria-haspopup="menu" aria-expanded={mentionsOpen}
                title={mentionsLabel(mentions.length)} aria-label={mentionsLabel(mentions.length)}
                onclick={toggleMentions}>
          <Icon name="message" size={15} />
          <span class="vmentions-badge">{mentions.length}</span>
        </button>
        {#if mentionsOpen}
          <div class="vmentions-pop" role="menu" aria-label={mentionsLabel(mentions.length)}>
            {#each mentions as row (row.stream_id)}
              <button class="vmentions-row" role="menuitem" onclick={() => openMention(row)}>
                <Icon name={mentionRowIcon(row)} size={14} />
                <span class="vmentions-row-title">{mentionRowTitle(row)}</span>
                {#if row.kind === 'run'}<span class="vmentions-row-kind">{m.viewer_run_badge()}</span>{/if}
              </button>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
    {#if editable}
      {#if dirty}
        <span class="vdirty" title={m.viewer_unsaved()} aria-label={m.viewer_unsaved()}>●</span>
      {/if}
      <!-- Save belongs to Edit; Preview shows only the dirty marker, never a dead button. -->
      {#if mode === 'edit'}
        <button class="vsave" disabled={!dirty || saving || conflict} onclick={save}
                title={m.viewer_save_title()} aria-label={m.action_save()}>
          {saving ? m.action_saving() : m.action_save()}
        </button>
      {/if}
    {/if}
    {#if copyable}
      <button class="cp" class:copied
              title={copied ? m.action_copied() : m.action_copy()} aria-label={m.action_copy()} onclick={copy}>
        <Icon name={copied ? 'check' : 'copy'} size={15} />
      </button>
    {/if}
    <button class="exp" aria-pressed={$previewExpanded}
            title={$previewExpanded ? m.viewer_collapse() : m.viewer_expand()}
            aria-label={$previewExpanded ? m.viewer_collapse() : m.viewer_expand()}
            onclick={() => previewExpanded.update((v) => !v)}>
      <Icon name={$previewExpanded ? 'minimize-2' : 'maximize-2'} size={15} />
    </button>
    {#if path}<a class="dl" href={downloadUrl} title={m.viewer_download()} aria-label={m.viewer_download()}><Icon name="download" size={15} /></a>{/if}
    <button class="rail-x" aria-label={m.action_close()} onclick={close}>×</button>
  </div>
  <div class="vbody" class:native class:editing={editable && mode === 'edit'}>
    {#if saveErr}<p class="vsaveerr" role="alert">{saveErr}</p>{/if}
    {#if conflict}
      <div class="vconflict" role="alert">
        <p class="vconflict-msg">{m.viewer_conflict_msg()}</p>
        <div class="vconflict-actions">
          <button class="vconflict-btn" disabled={saving} onclick={reloadFromDisk}>
            <span class="vconflict-verb">{m.viewer_reload()}</span>
            <span class="vconflict-note">{m.viewer_reload_note()}</span>
          </button>
          <button class="vconflict-btn" disabled={saving} onclick={overwrite}>
            <span class="vconflict-verb">{m.viewer_overwrite()}</span>
            <span class="vconflict-note">{m.viewer_overwrite_note()}</span>
          </button>
        </div>
      </div>
    {/if}
    {#if err}
      <!-- A path-backed load failed (missing/unreadable) → the shared missing panel;
           a path-less transient body can't go missing, so just show the raw error. -->
      {#if path}
        {@render missing('file-x', m.viewer_load_failed(), err)}
      {:else}
        <p class="muted" style="color:var(--danger)">{err}</p>
      {/if}
    {:else if kind === 'html'}
      <!-- agent HTML: scripts run but in an opaque origin (no allow-same-origin) -->
      <iframe class="vframe" title={title} src={url} sandbox="allow-scripts"></iframe>
    {:else if kind === 'pdf'}
      <iframe class="vframe" title={title} src={url}></iframe>
    {:else if kind === 'image'}
      {#if imgErr}
        {@render missing('image-off', m.viewer_image_failed())}
      {:else}
        <img class="vimg" src={url} alt={title} onerror={() => (imgErr = true)} />
      {/if}
    {:else if kind === 'code'}
      <pre class="vcode">{text}</pre>
    {:else if kind === 'text'}
      <pre class="vtext">{text}</pre>
    {:else if kind === 'download'}
      {#if dlView === 'raw'}
        {#if rawErr}
          {@render missing('file-x', m.viewer_raw_failed(), rawErr)}
        {:else}
          <pre class="vtext">{rawText}</pre>
        {/if}
      {:else}
        <p class="muted">
          {m.viewer_no_preview_1()}<button type="button" class="vlink" onclick={showRaw}>{m.viewer_no_preview_raw()}</button
          >{m.viewer_no_preview_or()}<a class="dl" href={downloadUrl}>{m.viewer_no_preview_download()}</a>{m.viewer_no_preview_2()}
        </p>
      {/if}
    {:else if editable && mode === 'edit'}
      <textarea class="vedit" bind:value={draft} spellcheck="false"
                aria-label={m.viewer_edit_aria({ name: name ?? '' })}></textarea>
    {:else}
      <Markdown text={draft} />
    {/if}
  </div>
</aside>
