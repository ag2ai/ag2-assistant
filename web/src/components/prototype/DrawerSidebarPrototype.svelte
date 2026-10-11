<script lang="ts">
  import { go, goTab, route } from '../../router.ts'
  import { switchProfile } from '../../controller.ts'
  import { profiles as profileStore, screenRows } from '../../store.ts'
  import { get as request } from '../../transport/http.ts'
  import { ChatList, type ChatRow } from '../../schemas/chat.ts'
  import { TaskList, type Task } from '../../schemas/task.ts'
  import { FilesListing, type FileRow } from '../../schemas/file.ts'
  import { ScreenList, type ScreenRow } from '../../schemas/screen.ts'
  import type { Tab } from '../../lib/route.ts'
  import Icon from '../Icon.svelte'
  import PrototypeActivityRail from './PrototypeActivityRail.svelte'
  import PrototypeSwitcher from './PrototypeSwitcher.svelte'
  import SidebarVariantAPrototype from './SidebarVariantAPrototype.svelte'
  import SidebarVariantBPrototype from './SidebarVariantBPrototype.svelte'
  import SidebarVariantCPrototype from './SidebarVariantCPrototype.svelte'
  import { sidebarVariant, sidebarSection, sidebarPanelOpen, keepPrototypeVariant } from './sidebarPrototype.ts'

  let chatItems = $state<ChatRow[]>([])
  let taskItems = $state<Task[]>([])
  let fileItems = $state<FileRow[]>([])
  let screenItems = $state<ScreenRow[]>([])
  let directories = $state<string[]>([])
  let query = $state('')
  let profilePicker = $state(false)
  let settingsPreview = $state(false)
  let draftOpen = $state(false)
  let draftText = $state('')
  let selectedFile = $state('')
  let fileText = $state('')
  let loading = $state(true)
  let listError = $state('')
  let collapsedDirectories = $state<string[]>([])
  const labels: Record<Tab, string> = { chats: 'Chats', tasks: 'Tasks', files: 'Files', screens: 'Screens' }
  const title = $derived(labels[$sidebarSection])
  const active = $derived($profileStore.list.find(profile => profile.id === $profileStore.activeId))
  const profileName = $derived(active?.name || 'Profile')
  const profileInitial = $derived(profileName.slice(0, 1).toUpperCase())

  $effect(() => {
    const pid = $profileStore.activeId
    if (!pid) return
    let stale = false
    loading = true
    listError = ''
    const prefix = '/api/p/' + encodeURIComponent(pid)
    void Promise.all([
      request(prefix + '/chats', ChatList), request(prefix + '/tasks', TaskList),
      request(prefix + '/files', FilesListing), request(prefix + '/screens', ScreenList),
    ]).then(([chatList, taskList, files, screens]) => {
      if (stale) return
      chatItems = chatList.chats; taskItems = taskList.tasks; fileItems = files.files
      directories = files.dirs; screenItems = screens.screens; $screenRows = screens.screens
      loading = false
    }).catch(cause => { if (!stale) { listError = String(cause); loading = false } })
    return () => { stale = true }
  })

  type Entry = { id: string; title: string; detail: string; icon: string; starred: boolean; dir: string }
  const entries = $derived.by((): Entry[] => {
    const tab = $sidebarSection
    const rows: Entry[] = tab === 'chats' ? chatItems.map(item => ({ id: item.chat_id, title: item.title || 'New chat', detail: item.preview, icon: 'message', starred: item.starred, dir: '' }))
      : tab === 'tasks' ? taskItems.map(item => ({ id: item.id, title: item.name, detail: item.schedule_desc || 'Manual', icon: 'list', starred: item.starred, dir: '' }))
      : tab === 'files' ? fileItems.map(item => ({ id: item.path, title: item.name, detail: item.dir, icon: item.name.endsWith('.yaml') ? 'file-code' : 'file-text', starred: false, dir: item.dir }))
      : screenItems.map(item => ({ id: item.path, title: item.title, detail: item.error || item.path, icon: 'screen', starred: false, dir: '' }))
    return rows.filter(item => (item.title + ' ' + item.detail).toLowerCase().includes(query.toLowerCase()))
  })
  const newLabel = $derived($sidebarSection === 'screens' ? 'Create with assistant' : $sidebarSection === 'files' ? 'New file' : $sidebarSection === 'tasks' ? 'New task' : 'New chat')

  function selectSection(tab: Tab) {
    if ($sidebarVariant === 'C' && tab === $sidebarSection) $sidebarPanelOpen = !$sidebarPanelOpen
    else $sidebarPanelOpen = true
    $sidebarSection = tab
    query = ''; draftOpen = false; selectedFile = ''; settingsPreview = false
  }
  function chooseProfile(pid: string) {
    switchProfile(pid)
    goTab('screens', pid)
    keepPrototypeVariant()
    $sidebarSection = 'screens'; profilePicker = false; query = ''
  }
  async function selectItem(entry: Entry) {
    if ($sidebarSection === 'files') {
      selectedFile = entry.id
      fileText = await fetch('/api/p/' + $profileStore.activeId + '/files/raw?path=' + encodeURIComponent(entry.id)).then(response => response.text())
      return
    }
    const path = $sidebarSection === 'screens' ? '/screens/s/' + encodeURIComponent(entry.id)
      : $sidebarSection === 'tasks' ? '/t/' + entry.id : '/c/' + entry.id
    go(path)
    keepPrototypeVariant()
  }
  function isSelected(entry: Entry) {
    return $sidebarSection === 'files' ? selectedFile === entry.id : $route.id === entry.id
  }

  $effect(() => console.info('[sidebar prototype]', {
    variant: $sidebarVariant, section: $sidebarSection, panelOpen: $sidebarPanelOpen,
    profile: profileName, selectedItem: selectedFile || $route.id, filter: query,
    draftOpen, draftText, profilePicker, settingsPreview, itemCount: entries.length,
  }))
</script>

{#snippet rail()}
  <PrototypeActivityRail {profileName} {profileInitial} onProfile={() => { profilePicker = !profilePicker; $sidebarPanelOpen = true }} onSettings={() => { settingsPreview = !settingsPreview; $sidebarPanelOpen = true }} onSection={selectSection} />
{/snippet}

{#snippet profiles()}
  <button class="profile-label" onclick={() => profilePicker = !profilePicker} aria-expanded={profilePicker} title="Switch profile">
    <span class="profile-dot" style="background:{active?.accent}"></span><span>{profileName}</span><Icon name="chevron-down" size={13} />
  </button>
{/snippet}

{#snippet create()}
  <button class="newrow" class:primary-create={$sidebarVariant === 'C'} onclick={() => { draftOpen = !draftOpen; draftText = '' }}><Icon name="plus" size={15} /> {newLabel}</button>
{/snippet}

{#snippet auxiliary()}
  {#if profilePicker}
    <div class="prototype-popover">
      <span class="group-label">PROFILES</span>
      {#each $profileStore.list as profile}
        <button class="profile-option" class:chosen={profile.id === active?.id} onclick={() => chooseProfile(profile.id)}><span class="profile-dot" style="background:{profile.accent}"></span>{profile.name}</button>
      {/each}
    </div>
  {/if}
  {#if settingsPreview}
    <div class="prototype-popover settings-preview"><strong>Settings</strong><p>Appearance, profiles, models, and tools</p><button class="small-button" onclick={() => settingsPreview = false}>Close</button></div>
  {/if}
  {#if draftOpen}
    <div class="draft"><div><strong>{newLabel}</strong><button aria-label="Close draft" onclick={() => draftOpen = false}><Icon name="x" size={13} /></button></div><textarea aria-label="Prototype draft" placeholder={$sidebarSection === 'screens' ? 'Describe your screen…' : 'Start here…'} bind:value={draftText}></textarea></div>
  {/if}
  {#if selectedFile}
    <div class="file-preview"><div><strong>{selectedFile.split('/').at(-1)}</strong><button aria-label="Close file preview" onclick={() => selectedFile = ''}><Icon name="x" size={13} /></button></div><pre>{fileText.slice(0, 2000)}</pre></div>
  {/if}
{/snippet}

{#snippet row(entry: Entry)}
  <button class="prototype-row" class:selected={isSelected(entry)} class:card={$sidebarVariant === 'C'} class:detailed={$sidebarVariant === 'B'}
    title={entry.detail || entry.title} onclick={() => void selectItem(entry)}>
    <span class="item-icon">{#if entry.icon === 'screen'}<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="3" y="3" width="18" height="14" rx="2" /><path d="M8 21h8M12 17v4" /></svg>{:else}<Icon name={entry.icon} size={15} strokeWidth={1.7} />{/if}</span>
    <span class="item-text"><span class="item-title">{entry.title}</span>{#if $sidebarVariant !== 'A' && entry.detail}<small>{entry.detail}</small>{/if}</span>
    {#if entry.starred}<span class="star"><Icon name="star" size={11} /></span>{/if}
  </button>
{/snippet}

{#snippet items()}
  {#if loading}<p class="empty-list">Loading…</p>{:else if listError}<p class="empty-list">{listError}</p>
  {:else if $sidebarSection === 'files' && !query}
    {#each directories.filter(dir => !dir.includes('/')) as dir}
      <button class="directory" onclick={() => collapsedDirectories = collapsedDirectories.includes(dir) ? collapsedDirectories.filter(item => item !== dir) : [...collapsedDirectories, dir]}><Icon name={collapsedDirectories.includes(dir) ? 'chevron-right' : 'chevron-down'} size={12} /><Icon name="folder" size={14} /> {dir}</button>
      {#if !collapsedDirectories.includes(dir)}<div class="nested">{#each entries.filter(entry => entry.dir === dir || entry.dir.startsWith(dir + '/')) as entry}{@render row(entry)}{/each}</div>{/if}
    {/each}
    {#each entries.filter(entry => !entry.dir) as entry}{@render row(entry)}{/each}
    {#if !entries.length && !directories.length}<p class="empty-list">No files yet.</p>{/if}
  {:else}
    {#if entries.some(entry => entry.starred)}
      <div class="group-label">Starred</div>{#each entries.filter(entry => entry.starred) as entry}{@render row(entry)}{/each}
      <div class="group-label">All {title.toLowerCase()}</div>
    {:else if entries.length}<div class="group-label">{$sidebarSection === 'screens' ? 'Saved screens' : $sidebarSection === 'chats' ? 'Recent' : $sidebarSection === 'tasks' ? 'All tasks' : 'Files'}</div>{/if}
    {#each entries.filter(entry => !entry.starred) as entry}{@render row(entry)}{/each}
    {#if !entries.length}<p class="empty-list">{query ? 'No matching items.' : `No ${title.toLowerCase()} yet.`}</p>{/if}
  {/if}
{/snippet}

{#if $sidebarVariant === 'B'}
  <SidebarVariantBPrototype {rail} {profiles} {items} {create} {auxiliary} {title} count={entries.length} bind:query />
{:else if $sidebarVariant === 'C'}
  <SidebarVariantCPrototype {rail} {profiles} {items} {create} {auxiliary} {title} count={entries.length} />
{:else}
  <SidebarVariantAPrototype {rail} {profiles} {items} {create} {auxiliary} {title} count={entries.length} />
{/if}
<PrototypeSwitcher />

<style>
  .profile-label { display: flex; align-items: center; gap: 8px; width: 100%; padding: 0; border: none; background: none; color: var(--text); text-align: left; font: 500 12px var(--font-sans); cursor: pointer; }
  .profile-label > span:nth-child(2) { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .profile-label:hover { color: var(--accent); }
  .profile-dot { width: 8px; height: 8px; flex: none; border-radius: 50%; }
  .group-label { display: block; padding: 8px 11px 6px; font-size: 10px; font-weight: 500; color: var(--text-faint); letter-spacing: .7px; text-transform: uppercase; }
  .prototype-row { display: flex; align-items: center; gap: 9px; width: 100%; min-width: 0; padding: 9px 10px; color: var(--text-muted); background: none; border: none; border-radius: 7px; text-align: left; font: 12px var(--font-sans); cursor: pointer; margin-bottom: 3px; }
  .prototype-row:hover { color: var(--text); background: var(--surface-hover); }
  .prototype-row.selected { color: var(--accent); background: var(--accent-soft); }
  .item-icon { flex: none; display: grid; place-items: center; opacity: .8; }
  .item-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
  .item-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .item-text small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; line-height: 1.4; color: var(--text-faint); }
  .star { color: var(--accent); flex: none; }
  .card { border: 1px solid var(--line); background: var(--surface); border-radius: 9px; margin-bottom: 8px; padding: 12px 10px; align-items: flex-start; }
  .card .item-icon { margin-top: 2px; }
  .card.selected { border-color: color-mix(in srgb, var(--accent) 35%, var(--line)); }
  .detailed { padding: 11px 9px; border-radius: 5px; border-bottom: 1px solid var(--line); margin-bottom: 1px; }
  .directory { display: flex; align-items: center; gap: 6px; width: 100%; border: none; padding: 9px 6px; background: none; color: var(--text-muted); font: 12px var(--font-sans); cursor: pointer; text-align: left; }
  .directory:hover { color: var(--text); }
  .nested { padding-left: 12px; }
  .primary-create { width: calc(100% - 12px); margin: 0 6px 4px; background: var(--accent-soft); color: var(--accent); border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--line)); font-size: 12px; }
  .prototype-popover, .draft, .file-preview { margin: 5px 12px 14px; padding: 10px; border: 1px solid var(--line); background: var(--surface); border-radius: 8px; }
  .profile-option { display: flex; align-items: center; gap: 8px; width: 100%; border: none; background: none; color: var(--text-muted); padding: 8px 0; cursor: pointer; text-align: left; font-size: 12px; }
  .profile-option.chosen, .profile-option:hover { color: var(--accent); }
  .draft > div, .file-preview > div { display: flex; align-items: center; gap: 6px; justify-content: space-between; }
  strong { font-size: 11px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; }
  .draft button, .file-preview button { border: none; background: none; color: var(--text-muted); cursor: pointer; padding: 3px; }
  textarea { width: 100%; min-height: 80px; margin-top: 10px; background: none; border: none; font: 12px var(--font-sans); color: var(--text); resize: vertical; outline: none; }
  pre { white-space: pre-wrap; overflow-wrap: anywhere; font: 10px var(--font-mono); max-height: 160px; overflow: auto; color: var(--text-muted); }
  .empty-list { padding: 10px; font-size: 12px; color: var(--text-faint); }
  .settings-preview p { color: var(--text-muted); font-size: 11px; line-height: 1.5; }
  .small-button { padding: 5px 10px; color: var(--text-muted); background: var(--surface-sunk); border: 1px solid var(--line); border-radius: 5px; cursor: pointer; }
</style>
