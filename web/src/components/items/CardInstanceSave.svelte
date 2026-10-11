<script lang="ts">
  import { onDestroy } from 'svelte'
  import CardSaveMenu from './CardSaveMenu.svelte'
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
  let opened = $state(false)
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
    if (busy || loading || epoch !== $profileEpoch || (result && !pending)) return
    pending ||= captureInstance(item, localData(), directory ? directory + '/' + filename : filename, crypto.randomUUID())
    busy = true; error = ''
    try {
      const saved = await api.saveCardInstance(originChat, pending)
      if (disposed || epoch !== $profileEpoch) return
      result = saved
      refreshFiles(saved.path)
      opened = false
      if (saved.history_recorded) pending = null
    } catch (cause) {
      if (disposed || epoch !== $profileEpoch) return
      error = errText(cause)
      if (isConflict(cause) && !result) pending = null
    } finally { busy = false }
  }
</script>

{#if result}
  <div class="instance-save-result">
    <span role="status">{result.message}</span>
    {#if error}<span role="alert">{error}</span>{/if}
    <button class="open" onclick={() => openAsideFile(result!.path)}>Open</button>
    {#if !result.history_recorded}
      <button class="open" disabled={busy} onclick={save}>{busy ? 'Recording…' : 'Retry confirmation'}</button>
    {/if}
  </div>
{/if}
<CardSaveMenu label="Save instance" icon="save" triggerClass="instance-save-trigger" bind:opened onOpen={open}>
  <form onsubmit={(event) => { event.preventDefault(); save() }}>
    <h2>Save instance</h2>
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
    <div class="card-save-actions">
      <button type="button" class="open" disabled={busy} onclick={() => opened = false}>Cancel</button>
      <button type="submit" class="open primary" disabled={busy || loading || !filename.endsWith(INSTANCE_SUFFIX) || !filename.trim()}>{busy ? 'Saving…' : pending ? 'Retry Save' : 'Save instance'}</button>
    </div>
  </form>
</CardSaveMenu>

<style>
  .instance-save-result { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 8px 0 16px; font-size: 12px; }
</style>
