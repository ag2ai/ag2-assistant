<script>
  import { onMount } from 'svelte'
  import { thread, SETTINGS_PAGE, profiles } from '../store.js'
  import { openOverlay } from '../router.js'
  import { send, stop, startVoice, stopVoice, voice } from '../controller.js'
  import { liveConfigs, loadLiveConfigs } from '../lib/live.js'
  import { llmConfigs } from '../lib/llm.js'
  import { api } from '../transport/api.js'
  import { getActiveProfileId } from '../lib/profile.js'
  import { chatChips, profileExtraCount, addPlan } from '../lib/chatFolders.js'
  import { foldersStore, loadFolders, applyFolders } from '../lib/folders.js'
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
    api.settings().then((s) => { fsRoots = s.fs || {} }).catch(() => {})
  })

  // Per-chat Folder access (CONTEXT.md "Grant"): only the chat-scoped layer shows
  // as removable chips; profile reach shows as a "+N profile folder" note.
  const pid = $derived($profiles.activeId || getActiveProfileId())
  const chatId = $derived($thread.chat)
  // An empty chatId means PROFILE scope in setGrant, so never expose it without one.
  const showFolders = $derived($thread.kind === 'chat' && !!chatId)

  const folders = $derived($foldersStore.folders)   // shared snapshot {id,name,path,exists,grants[]}
  let fsRoots = $state({})
  let picking = $state(false)       // FolderPicker modal open
  let foldersModal = $state(false)  // ChatFolders (mode pills) modal open
  let folderBusy = $state(false)
  let folderErr = $state('')
  let folderNote = $state('')

  const chips = $derived(showFolders ? chatChips(folders, pid, chatId) : [])
  const extraCount = $derived(showFolders ? profileExtraCount(folders, pid, chatId) : 0)

  // Busy/error wrapper for a folder op; leaves `folders` to the op itself.
  async function folderOp(fn) {
    folderErr = ''; folderNote = ''; folderBusy = true
    try { await fn() } catch (e) { folderErr = String(e.message || e) }
    folderBusy = false
  }
  // No per-chat reload: the shared snapshot holds every chat's grants, so the
  // chip strip re-derives (via chatChips/chatId) when you switch chats.

  function openPicker() { folderErr = ''; folderNote = ''; picking = true }

  // Pick a path → mint (or reuse on 409) the Folder → grant this chat read, unless
  // a profile Grant already covers it (then just say so).
  const addFolder = (path) => folderOp(async () => {
    let snap, folder
    try {
      snap = await api.createFolder(path)
      folder = (snap.folders || []).find((f) => f.path === path)
    } catch (e) {
      if (e.status === 409 && e.body?.existing?.id) {
        snap = await api.folders()
        folder = (snap.folders || []).find((f) => f.id === e.body.existing.id)
      } else throw e
    }
    if (!folder) throw new Error('Could not resolve that folder')
    const plan = addPlan(folder, pid, chatId)
    if (plan.status === 'grant') snap = await api.setGrant(folder.id, pid, 'read', chatId)
    else if (plan.status === 'unblock') { snap = await api.revokeGrant(folder.id, pid, chatId); folderNote = `"${plan.name}" is available from the profile again.` }
    else if (plan.status === 'covered') folderNote = `"${plan.name}" is already available from the profile.`
    else if (plan.status === 'exists') folderNote = `"${folder.name}" is already in this chat.`
    applyFolders(snap)
    picking = false
  })

  const removeChip = (f) => folderOp(async () => {
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
  let pending = $state([])  // {name, payload:{name,mime,data(b64)}}
  let ta, fileInput

  function submit() {
    const t = text.trim()
    if (!t && !pending.length) return
    send(t, pending.map((p) => p.payload))
    text = ''; pending = []
    if (ta) ta.style.height = 'auto'
  }
  function key(e) {
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

  function toB64(file) {
    return new Promise((res, rej) => {
      const r = new FileReader()
      r.onload = () => res(String(r.result).split(',')[1] || '')
      r.onerror = rej
      r.readAsDataURL(file)
    })
  }
  async function pick(e) {
    for (const f of e.target.files) {
      const data = await toB64(f)
      pending = [...pending, { name: f.name, payload: { name: f.name, mime: f.type, data } }]
    }
    if (fileInput) fileInput.value = ''
  }
  const removeFile = (i) => { pending = pending.filter((_, j) => j !== i) }
</script>

<div class="composer">
  <div class="inputbox" class:busy={$thread.busy}>
    {#if showFolders && (extraCount || chips.length)}
      <div class="cfolders">
        {#if extraCount}
          <button class="cfmore" title="Folder access from your profile" onclick={() => (foldersModal = true)}>+{extraCount} profile folder{extraCount === 1 ? '' : 's'}</button>
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
    <textarea
      class="cinput"
      bind:this={ta}
      bind:value={text}
      rows="1"
      placeholder={placeholder()}
      oninput={grow}
      onkeydown={key}
    ></textarea>
    <div class="cbar">
      <button class="cbtn" onclick={() => fileInput.click()} title="Attach files" aria-label="Attach files"><Icon name="plus" size={18} /></button>
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
  <div class="modal-backdrop over" onclick={() => (picking = false)}></div>
  <div class="modal over">
    <h2>Add a folder to this chat</h2>
    <p class="muted cfhint">Gives this conversation <b>read</b> access to a folder outside the workspace. Change the mode or remove it anytime from the chip.</p>
    {#if folderErr}<p class="cfmsg err">{folderErr}</p>{/if}
    <FolderPicker roots={fsRoots} start={fsRoots.cwd || fsRoots.home || ''} busy={folderBusy} onUse={addFolder} />
    <button class="modal-close" onclick={() => (picking = false)}>Cancel</button>
  </div>
{/if}
{#if foldersModal}
  <ChatFolders {chatId} onClose={closeFoldersModal} />
{/if}

<style>
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
  .cfmsg.err { color: #d8552f; }
  .cfhint { font-size: 12px; margin: 0 0 10px; }
</style>
