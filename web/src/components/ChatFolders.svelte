<script>
  // Per-chat Folder access (CONTEXT.md "Grant"): this chat's Grants ADD to the
  // profile's — profile-wide access is shown read-only here for context; the
  // pills manage only the chat-scoped layer.
  import { onMount } from 'svelte'
  import { api } from '../transport/api.js'
  import { profiles } from '../store.js'
  import { getActiveProfileId } from '../lib/profile.js'
  import Icon from './Icon.svelte'

  let { chatId, onClose } = $props()

  let folders = $state([])
  let busy = $state(false)
  let err = $state('')
  const pid = $derived($profiles.activeId || getActiveProfileId())

  const apply = (r) => { folders = r.folders || [] }
  onMount(async () => {
    try { apply(await api.folders()) } catch (e) { err = String(e.message || e) }
  })
  async function run(fn) {
    err = ''; busy = true
    try { apply(await fn()) } catch (e) { err = String(e.message || e) }
    busy = false
  }

  const chatGrant = (f) => (f.grants || []).find((g) => g.profile === pid && g.chat_id === chatId)
  const profileGrant = (f) => (f.grants || []).find((g) => g.profile === pid && !g.chat_id)
  function setMode(f, mode) {
    const cur = chatGrant(f)
    if (!mode) { if (cur) run(() => api.revokeGrant(f.id, pid, chatId)); return }
    if (cur?.mode === mode) return
    run(() => api.setGrant(f.id, pid, mode, chatId))
  }
</script>

<div class="modal-backdrop over" onclick={onClose}></div>
<div class="modal over">
  <h2>Folder access — this chat</h2>
  <p class="muted hint">Extra reach for this conversation only. Profile-wide access already applies here and can't be narrowed.</p>
  {#if err}<p class="muted errline">{err}</p>{/if}
  {#if !folders.length}
    <p class="muted">No folders registered. Add one in Settings → Folders first.</p>
  {/if}
  {#each folders as f (f.id)}
    {@const pg = profileGrant(f)}
    {@const cg = chatGrant(f)}
    <div class="cfrow">
      <span class="cfico"><Icon name="folder" size={14} /></span>
      <span class="cfname" title={f.path}>{f.name}</span>
      {#if pg}
        <span class="cfprofile">profile: {pg.mode === 'read_write' ? 'read + write' : 'read'}</span>
      {/if}
      <span class="focuspills">
        <button class="focuspill" class:on={!cg} disabled={busy} onclick={() => setMode(f, null)}>Off</button>
        <button class="focuspill" class:on={cg?.mode === 'read'} disabled={busy} onclick={() => setMode(f, 'read')}>Read</button>
        <button class="focuspill" class:on={cg?.mode === 'read_write'} disabled={busy} onclick={() => setMode(f, 'read_write')}>Read + write</button>
      </span>
    </div>
  {/each}
  <button class="modal-close" onclick={onClose}>Close</button>
</div>

<style>
  .hint { font-size: 12px; margin: 0 0 10px; }
  .errline { color: #d8552f; font-size: 13px; }
  .cfrow { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--line); }
  .cfico { flex: none; display: inline-flex; color: var(--text-muted); }
  .cfname { flex: 1; min-width: 0; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cfprofile { flex: none; font-size: 11px; color: var(--text-muted); }
</style>
