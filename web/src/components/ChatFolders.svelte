<script>
  // Folder access for ONE chat (CONTEXT.md "Grant") — everything here affects
  // this conversation only, never other chats. Chat folders (this chat's own
  // Grants) get a 2-position iOS switch (Read / Read+write); Delete + Move to
  // task/profile live in a kebab. Task and Profile folders (shared across the
  // task's runs, resp. the whole profile) get a 3-position slider (Read /
  // Read+write / Off) writing a chat-scoped OVERRIDE — Off blocks the folder for
  // this chat only, without touching the install-wide task/profile Grant.
  // Clicking a switch advances it to the next position. Only folders that
  // already reach this chat are listed; new ones added from composer. `taskId`
  // is only ever set for a run's thread — plain chats get no Task folders section.
  import { onMount } from 'svelte'
  import { api } from '../transport/api.js'
  import { profiles } from '../store.js'
  import { foldersStore, loadFolders, applyFolders } from '../lib/folders.js'
  import { getActiveProfileId } from '../lib/profile.js'
  import Icon from './Icon.svelte'
  import AccessSwitch from './AccessSwitch.svelte'
  import WriteSwitch from './WriteSwitch.svelte'

  let { chatId, taskId = '', onClose } = $props()

  let busy = $state(false)
  let err = $state('')
  let menuFor = $state('')   // id of the chat folder whose overflow menu is open
  const pid = $derived($profiles.activeId || getActiveProfileId())
  // Shared snapshot: mutations here also refresh the composer chips + Settings live.
  const folders = $derived($foldersStore.folders)

  onMount(() => { if (!$foldersStore.loaded) loadFolders() })
  async function run(fn) {
    err = ''; busy = true
    try { applyFolders(await fn()) } catch (e) { err = String(e.message || e) }
    busy = false
  }

  const chatGrant = (f) => (f.grants || []).find((g) => g.profile === pid && g.chat_id === chatId)
  const profileGrant = (f) => (f.grants || []).find((g) => g.profile === pid && !g.chat_id && !g.task_id)
  const tGrant = (f) => (taskId ? (f.grants || []).find((g) => g.profile === pid && g.task_id === taskId && !g.chat_id) : null)
  // Наследуемая база чата: task-грант переопределяет профильный (chat > task > profile).
  const inheritedMode = (f) => tGrant(f)?.mode ?? profileGrant(f)?.mode
  // Profile/task folders may carry a per-chat override; chat folders are chat-only additions.
  const taskFolders = $derived(taskId ? folders.filter((f) => tGrant(f)) : [])
  const profileFolders = $derived(folders.filter((f) => profileGrant(f) && !tGrant(f)))
  const chatFolders = $derived(folders.filter((f) => chatGrant(f) && !profileGrant(f) && !tGrant(f)))
  // Effective access for THIS chat: a chat grant (incl. 'none' = off) overrides the inherited (task/profile) grant.
  const effMode = (f) => { const c = chatGrant(f); return c ? c.mode : inheritedMode(f) }

  // Chat-only folders: null mode = revoke this chat's grant.
  function setChatMode(f, mode) {
    const cur = chatGrant(f)
    if (!mode) { if (cur) run(() => api.revokeGrant(f.id, pid, chatId)); return }
    if (cur?.mode === mode) return
    run(() => api.setGrant(f.id, pid, mode, chatId))
  }
  // Promote a chat-only folder to the profile (reachable from every chat): mint a
  // profile-scope grant at the same mode, then drop the now-redundant chat grant.
  function moveToProfile(f) {
    const cur = chatGrant(f)
    if (!cur) return
    const mode = cur.mode === 'read_write' ? 'read_write' : 'read'
    run(async () => {
      await api.setGrant(f.id, pid, mode)          // profile scope (no chatId)
      return api.revokeGrant(f.id, pid, chatId)    // returns the merged snapshot
    })
  }
  // Move a chat-only folder up to the task scope: every run under this task inherits
  // it, not just this chat. Mints a task-scope grant, then drops the redundant chat one.
  function moveToTask(f) {
    const cur = chatGrant(f)
    if (!cur || !taskId) return
    const mode = cur.mode === 'read_write' ? 'read_write' : 'read'
    run(async () => {
      await api.setGrant(f.id, pid, mode, '', taskId)   // task scope
      return api.revokeGrant(f.id, pid, chatId)
    })
  }
  // Profile/task folders: write a chat-scoped OVERRIDE (this chat only). null/'none'
  // blocks; setting it back to the inherited (task, else profile) mode drops the override.
  function setChatOverride(f, mode) {
    const target = mode || 'none'
    if (effMode(f) === target) return
    const cur = chatGrant(f)
    if (target === inheritedMode(f)) { if (cur) run(() => api.revokeGrant(f.id, pid, chatId)); return }
    run(() => api.setGrant(f.id, pid, target, chatId))
  }
</script>

<div class="modal-backdrop over" onclick={onClose}></div>
<div class="modal over">
  <button class="modal-x" aria-label="Close" onclick={onClose}>×</button>
  <h2>Folder access — this chat</h2>
  <p class="muted hint">Everything here affects this chat only. Profile folders are shared across the profile — changing one here overrides it for this conversation, leaving other chats untouched.{#if taskId}{' '}Task folders are shared across this task's runs — changing one here overrides it for this thread only.{/if}</p>
  {#if err}<p class="muted errline">{err}</p>{/if}
  {#if !taskFolders.length && !profileFolders.length && !chatFolders.length}
    <p class="muted">No folders in this chat yet. Use the folder button in the composer to add one.</p>
  {/if}

  {#if chatFolders.length}
    {#each chatFolders as f (f.id)}
      {@const cg = chatGrant(f)}
      <div class="cfrow">
        <span class="cfico"><Icon name="folder" size={14} /></span>
        <span class="cfname" title={f.path}>{f.name}</span>
        <div class="cfctl">
          <WriteSwitch mode={cg?.mode} disabled={busy} onchange={(m) => setChatMode(f, m)} />
          <span class="cfmenuwrap">
            <button class="cfkebab" aria-label="More actions" aria-expanded={menuFor === f.id} disabled={busy} onclick={() => (menuFor = menuFor === f.id ? '' : f.id)}>⋯</button>
            {#if menuFor === f.id}
              <div class="cfscrim" onclick={() => (menuFor = '')}></div>
              <div class="cfmenu">
                {#if taskId}
                  <button onclick={() => { menuFor = ''; moveToTask(f) }}><Icon name="list" size={14} /> Move to task</button>
                {/if}
                <button onclick={() => { menuFor = ''; moveToProfile(f) }}><Icon name="users" size={14} /> Move to profile</button>
                <button class="danger" onclick={() => { menuFor = ''; setChatMode(f, null) }}><Icon name="trash" size={14} /> Delete</button>
              </div>
            {/if}
          </span>
        </div>
      </div>
    {/each}
  {/if}

  {#if taskFolders.length}
    <div class="cfsec">Task folders</div>
    {#each taskFolders as f (f.id)}
      <div class="cfrow">
        <span class="cfico"><Icon name="folder" size={14} /></span>
        <span class="cfname" title={f.path}>{f.name}</span>
        <AccessSwitch mode={effMode(f)} disabled={busy} onchange={(m) => setChatOverride(f, m)} />
      </div>
    {/each}
  {/if}

  {#if profileFolders.length}
    <div class="cfsec">Profile folders</div>
    {#each profileFolders as f (f.id)}
      <div class="cfrow">
        <span class="cfico"><Icon name="folder" size={14} /></span>
        <span class="cfname" title={f.path}>{f.name}</span>
        <AccessSwitch mode={effMode(f)} disabled={busy} onchange={(m) => setChatOverride(f, m)} />
      </div>
    {/each}
  {/if}
</div>

<style>
  h2 { padding-right: 34px; }
  .hint { font-size: 12px; margin: 0 0 10px; }
  .errline { color: #d8552f; font-size: 13px; }
  .cfsec { font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--text-muted); margin: 16px 0 6px; }
  .cfrow { display: flex; align-items: center; gap: 10px; padding: 8px 0; }
  /* Divider only between adjacent folders — the section header and Close button
     break adjacency, so the last row of each section has no trailing border. */
  .cfrow + .cfrow { border-top: 1px solid var(--line); }
  .cfico { flex: none; display: inline-flex; color: var(--text-muted); }
  .cfname { flex: 1; min-width: 0; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cfctl { flex: none; display: inline-flex; align-items: center; gap: 10px; }

  /* Overflow menu — Move to profile / Delete tucked behind a quiet kebab. */
  .cfmenuwrap { position: relative; flex: none; }
  .cfkebab { background: none; border: none; cursor: pointer; color: var(--text-muted); font-size: 17px; line-height: 1; padding: 2px 7px; border-radius: 6px; }
  .cfkebab:hover { color: var(--ink); background: var(--code); }
  .cfkebab:disabled { cursor: default; }
  .cfscrim { position: fixed; inset: 0; z-index: 40; }
  .cfmenu { position: absolute; right: 0; top: 100%; margin-top: 5px; z-index: 41; min-width: 170px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; box-shadow: 0 10px 28px rgba(0, 0, 0, .28); padding: 5px; display: flex; flex-direction: column; gap: 2px; }
  .cfmenu button { display: flex; align-items: center; gap: 9px; width: 100%; text-align: left; background: none; border: none; padding: 8px 10px; font: inherit; font-size: 13px; color: var(--ink); cursor: pointer; border-radius: 7px; }
  .cfmenu button:hover { background: var(--code); }
  .cfmenu button.danger { color: #d8552f; }
</style>
