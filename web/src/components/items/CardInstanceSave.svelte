<script lang="ts">
  import { onDestroy } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { thread, profileEpoch, refreshFiles } from '../../store.ts'
  import { openAsideFile } from '../../router.ts'
  import { captureInstance, instanceFilename, INSTANCE_SUFFIX } from '../../lib/cardInstances.ts'
  import { errText } from '../../lib/errors.ts'
  import { isConflict } from '../../lib/fileEdit.ts'
  import type { ThreadItem } from '../../schemas/events.ts'
  import type { A2UIData } from '../../lib/a2ui.ts'
  import type { CardInstanceSaveRequest, CardInstanceSave } from '../../schemas/card_instance.ts'

  let { item, localData }: { item: Extract<ThreadItem, { kind: 'a2ui' }>; localData: () => A2UIData } = $props()
  let dialog: HTMLDialogElement
  let directory = $state('')
  let filename = $state('')
  let directories = $state<string[]>([])
  let paths = $state<string[]>([])
  let busy = $state(false)
  let loading = $state(false)
  let error = $state('')
  let result = $state<CardInstanceSave | null>(null)
  let pending = $state.raw<CardInstanceSaveRequest | null>(null)
  let originChat = ''
  let epoch = 0
  let disposed = false
  onDestroy(() => { disposed = true })

  async function open() {
    epoch = $profileEpoch
    originChat = $thread.chat
    directory = ''; error = ''; result = null; pending = null
    directories = []; paths = []; filename = ''
    dialog.showModal()
    loading = true
    try {
      const files = await api.files()
      if (disposed || epoch !== $profileEpoch) return
      directories = files.dirs || []
      paths = files.files.map(file => file.path)
      filename = instanceFilename(item.title || '', directory, paths)
    } catch (cause) { error = errText(cause) }
    finally { loading = false }
  }

  function changeDirectory(event: Event & { currentTarget: HTMLSelectElement }) {
    directory = event.currentTarget.value
    pending = null
    filename = instanceFilename(item.title || '', directory, paths)
    error = ''
  }

  async function save() {
    if (busy || loading || epoch !== $profileEpoch) return
    pending ||= captureInstance(item, localData(), directory ? directory + '/' + filename : filename, crypto.randomUUID())
    busy = true; error = ''
    try {
      const saved = await api.saveCardInstance(originChat, pending)
      if (disposed || epoch !== $profileEpoch) return
      result = saved
      refreshFiles(saved.path)
      dialog.close()
      if (saved.history_recorded) pending = null
    } catch (cause) {
      if (disposed || epoch !== $profileEpoch) return
      error = errText(cause)
      if (isConflict(cause)) pending = null
    } finally { busy = false }
  }
</script>

<div class="instance-save">
  <button class="open" onclick={open}>Save instance</button>
  {#if result}
    <span role="status">{result.message}</span>
    <button class="open" onclick={() => openAsideFile(result!.path)}>Open</button>
    {#if !result.history_recorded}
      <button class="open" disabled={busy} onclick={save}>{busy ? 'Recording…' : 'Retry confirmation'}</button>
    {/if}
  {/if}
</div>
<dialog class="instance-save-dialog" bind:this={dialog} aria-label="Save instance as" oncancel={(event) => { if (busy) event.preventDefault() }}>
  <form onsubmit={(event) => { event.preventDefault(); save() }}>
    <h2>Save instance as</h2>
    <label>Directory
      <select value={directory} onchange={changeDirectory} disabled={busy || loading}>
        <option value="">Files</option>
        {#each directories as dir}<option value={dir}>{dir}</option>{/each}
      </select>
    </label>
    <label>Filename
      <input bind:value={filename} oninput={() => { pending = null; error = '' }} disabled={busy || loading} required />
    </label>
    <p class="muted">Keep the {INSTANCE_SUFFIX} suffix.</p>
    {#if loading}<p role="status">Loading Directories…</p>{/if}
    {#if error}<p role="alert">{error}</p>{/if}
    <div class="buttons">
      <button type="button" class="open" disabled={busy} onclick={() => dialog.close()}>Cancel</button>
      <button type="submit" class="open" disabled={busy || loading || !filename.endsWith(INSTANCE_SUFFIX) || !filename.trim()}>{busy ? 'Saving…' : pending ? 'Retry Save' : 'Save instance'}</button>
    </div>
  </form>
</dialog>

<style>
  .instance-save { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 8px 0 16px; font-size: 12px; }
  dialog { color: var(--text); background: var(--bg); border: 1px solid var(--line); border-radius: 16px; width: min(440px, 90vw); padding: 24px; }
  dialog::backdrop { background: #0007; }
  form { display: flex; flex-direction: column; gap: 12px; }
  h2 { font-size: 18px; margin: 0 0 8px; }
  label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
  input, select { padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; color: var(--text); background: var(--bg); }
  p { margin: 0; font-size: 12px; }
  .buttons { display: flex; justify-content: end; gap: 12px; }
</style>
