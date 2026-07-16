<script>
  import { onMount } from 'svelte'
  import { chats, tasks, drawerTab, settingsOpen, filesOpen, profiles } from '../store.js'
  import { route, go, newChatId } from '../router.js'
  import { api } from '../transport/api.js'
  import Icon from './Icon.svelte'
  import ProfileForm from './ProfileForm.svelte'
  import { fmtWhen, fmtNextIn, fmtAgoShort, dayRows, fmtDayShort } from '../lib/time.js'
  import ag2Logo from '../assets/ag2.svg'
  import ag2LogoWhite from '../assets/ag2-white.svg'
  import { inkOn } from '../design/palette.js'

  // Compact form of a cron description for the narrow schedule tag: abbreviate
  // day names and collapse "Every hour, between X and Y" → "Hourly X–Y"
  // ("Every hour, between 04:00 and 14:59, Monday through Friday" →
  // "Hourly 04:00–14:59, Mon–Fri"). Full text stays in the tooltip alongside
  // the raw cron.
  const DAY_ABBR = { Monday: 'Mon', Tuesday: 'Tue', Wednesday: 'Wed', Thursday: 'Thu', Friday: 'Fri', Saturday: 'Sat', Sunday: 'Sun' }
  const shortSched = (desc) => (desc || '')
    .replace(/\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b/g, (d) => DAY_ABBR[d])
    .replace(/\bEvery hour, between (\S+) and (\S+)/, 'Hourly $1–$2')
    .replace(/\b[Bb]etween (\S+) and (\S+)/, '$1–$2')
    .replace(/ through /g, '–')

  // Profile switcher chips (§5.4). A row of accent-tinted monogram chips at the
  // top of the Drawer: the active profile filled, others outlined; click switches
  // (full-page nav — App.svelte's boot makes the URL pid the persisted choice and
  // applies its accent). >4 profiles collapse the overflow into a small anchored
  // picker. Each profile's accent is a #rrggbb hex applied directly.
  const list = $derived($profiles.list || [])
  // ⌘1..9 shortcut hint for a chip's tooltip (§5.4): the profile's 1-based index in
  // registry order, shown only for the first 9 (the shortcut range). ⌘ on mac, Ctrl
  // elsewhere. The keydown handler itself lives once in App.svelte.
  const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || '')
  const shortcutHint = (pid) => {
    const i = list.findIndex((p) => p.id === pid)
    return i >= 0 && i < 9 ? ` — ${isMac ? '⌘' : 'Ctrl+'}${i + 1}` : ''
  }
  const chipTitle = (p) => p.name + shortcutHint(p.id)
  const active = $derived(list.find((p) => p.id === $profiles.activeId))
  const initial = (p) => (p?.name || '?').trim().charAt(0).toUpperCase() || '?'

  const switchTo = (pid) => location.assign('/app/' + pid + '/')

  // Chips shown inline vs. collapsed. ≤4 profiles: all inline, no menu. >4: show
  // the first few inline and fold the rest into a "+N" overflow picker (which also
  // includes the active one so it's always reachable/marked).
  const MAX_CHIPS = 4
  const inlineChips = $derived(list.length <= MAX_CHIPS ? list : list.slice(0, MAX_CHIPS - 1))
  const overflow = $derived(list.length <= MAX_CHIPS ? [] : list.slice(MAX_CHIPS - 1))

  let pickerOpen = $state(false)
  // Close the picker on click-outside / Escape.
  function onDocPointer(e) {
    if (pickerOpen && !e.target.closest('.profchips')) pickerOpen = false
    if (menuChat && !e.target.closest('.chatmenu') && !e.target.closest('.rowkebab')) menuChat = ''
  }
  function onDocKey(e) {
    if (createOpen && e.key === 'Escape') { createOpen = false; return }
    if (pickerOpen && e.key === 'Escape') pickerOpen = false
    if (menuChat && e.key === 'Escape') menuChat = ''
  }

  // "+" chip → profile-creation modal (§5.4). Reuses ProfileForm (same form as
  // onboarding). Preset accents already claimed by existing profiles are hidden
  // (a custom colour is always available). On success → full-page navigate to
  // /app/{pid}/ (App.svelte's boot adopts it and applies its accent).
  let createOpen = $state(false)
  const claimedAccents = $derived(list.map((p) => p.accent))
  async function createProfile({ name, accent }) {
    const res = await api.createProfile(name, accent) // throws → inline
    location.assign('/app/' + res.profile.id + '/')
  }

  let usageAll = $state(null) // install-wide roll-up {profiles:[{pid,name,...}], total}
  // The active profile's own totals, derived from the roll-up (one request, not two).
  const usage = $derived((usageAll?.profiles || []).find((p) => p.pid === $profiles.activeId) || null)
  let statusById = $state({}) // pid -> {busy, running_tasks, unseen_done} from GET /api/status
  // A chip's dot: true when that profile has finished tasks the user hasn't opened
  // yet (rolls up the nav's per-row unread marker to the profile). Clears on the
  // next 5s poll once the run is opened (markSeen).
  const hasUnseen = (pid) => (statusById[pid]?.unseen_done || 0) > 0

  async function refresh() {
    try {
      const server = await api.chats()
      const ids = new Set(server.map((s) => s.chat_id))
      // keep optimistic, not-yet-persisted chats (just sent, agent still replying)
      $chats = [...$chats.filter((s) => !ids.has(s.chat_id)), ...server]
    } catch {}
    try { $tasks = await api.tasksAll('all') } catch {}
    // One global roll-up serves both the active profile's line and the install-wide
    // "all" total; the per-profile /usage route stays for API users.
    try { usageAll = await api.usageAll() } catch {}
    // Piggyback per-profile activity on the same 5s cycle (§5.4 activity badges).
    // /api/status is global (all profiles) — index it by pid for the chips.
    try {
      const rows = await api.status()
      statusById = Object.fromEntries((rows || []).map((r) => [r.pid, r]))
    } catch {}
  }

  const fmtTok = (n) =>
    !n ? '0' : n < 1000 ? String(Math.round(n)) : `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k`
  // "12.3k tok · ~$0.0456" for a usage-shaped {total, cost, priced}. Cost is an
  // estimate (~) and only shown when the contributing model(s) had known pricing.
  const fmtUsage = (u) => {
    const tok = `${fmtTok(u.total)} tok`
    return u.priced ? `${tok} · ~$${u.cost.toFixed(u.cost < 1 ? 3 : 2)}` : tok
  }
  // >1 profile → the install-wide roll-up is meaningful; a single profile's "all"
  // would just repeat its own line, so it's suppressed.
  const multiProfile = $derived((usageAll?.profiles || []).length > 1)
  const usageLabel = $derived.by(() => {
    if (!usage || !usage.total) return ''
    let label = fmtUsage(usage)
    // Append the install-wide total only when more than one profile exists.
    if (multiProfile && usageAll?.total?.total) label += ` · all: ${fmtUsage(usageAll.total)}`
    return label
  })
  const usageTitle = $derived.by(() => {
    if (!usage) return ''
    const models = Object.keys(usage.by_model || {}).join(', ')
    let title = `Today (${usage.date}): ${fmtTok(usage.prompt)} in / ${fmtTok(usage.completion)} out`
      + (usage.priced ? ` · ~$${(usage.cost || 0).toFixed(4)} (estimate)` : ' · cost: no price set')
      + (models ? `\nmodels: ${models}` : '')
    // Per-profile breakdown line ("Work: … · Personal: …") when more than one exists.
    if (multiProfile) {
      const breakdown = (usageAll.profiles || [])
        .filter((p) => p.total)
        .map((p) => `${p.name}: ${fmtUsage(p)}`)
        .join(' · ')
      if (breakdown) title += `\nall profiles — ${breakdown}`
    }
    return title
  })
  onMount(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    document.addEventListener('pointerdown', onDocPointer, true)
    document.addEventListener('keydown', onDocKey)
    return () => {
      clearInterval(t)
      document.removeEventListener('pointerdown', onDocPointer, true)
      document.removeEventListener('keydown', onDocKey)
    }
  })

  // Chats grouped under date section headers by last-message time. `updated` is
  // the chat's last-update ISO stamp (rewritten on every message → respects
  // the latest message, not the first); dayRows tags the first row of each
  // calendar day with `sep`, the fmtDayShort header ("Recent"/"Yesterday"/date).
  // The list already arrives newest-first, so the walk just detects day changes.
  // Starred chats pin to a "Starred" section above the date groups (they appear
  // ONLY there); the rest group under date headers by last-message time.
  const starredChats = $derived($chats.filter((c) => c.starred))
  const chatRows = $derived(
    dayRows($chats.filter((c) => !c.starred).map((s) => ({ ...s, at: s.updated })), fmtDayShort)
  )

  const openChat = (id) => go('/c/' + id)
  const openTask = (id) => go('/t/' + id)
  const newChat = () => go('/c/' + newChatId())

  // Chat delete: a hover-revealed trash on the row swaps to an inline "Delete? yes/no"
  // confirm (mirrors the Files modal). Permanent — the backend drops the transcript
  // AND the full event log. If the open chat is the one deleted, hop to a fresh chat.
  let confirmChat = $state('') // chat_id awaiting delete confirmation
  let busyChat = $state('') // chat_id currently being deleted
  async function delChat(id) {
    busyChat = id
    try {
      await api.deleteChat(id)
      $chats = $chats.filter((s) => s.chat_id !== id)
      if ($route.name === 'chat' && $route.id === id) go('/c/' + newChatId())
    } catch {}
    busyChat = ''
    confirmChat = ''
  }

  // Row kebab menu (Star / Rename / Delete). One menu at a time, anchored to the
  // kebab with position:fixed so the scrolling list can't clip it; closes on
  // outside pointer, Escape, scroll, or action.
  let menuChat = $state('') // chat_id whose menu is open
  let menuPos = $state({ x: 0, y: 0 })
  function toggleMenu(e, s) {
    e.stopPropagation()
    if (menuChat === s.chat_id) { menuChat = ''; return }
    const r = e.currentTarget.getBoundingClientRect()
    menuPos = { x: r.right, y: r.bottom + 4 }
    menuChat = s.chat_id
  }

  async function toggleStar(s) {
    menuChat = ''
    const next = !s.starred
    $chats = $chats.map((c) => (c.chat_id === s.chat_id ? { ...c, starred: next } : c))
    try { await api.updateChat(s.chat_id, { starred: next }) } catch {
      $chats = $chats.map((c) => (c.chat_id === s.chat_id ? { ...c, starred: !next } : c))
    }
  }

  // Inline rename: the row's label becomes an input; Enter/blur commit, Escape
  // cancels, empty commit = cancel. A user title is authoritative server-side.
  let renameChat = $state('') // chat_id being renamed
  let renameText = $state('')
  function startRename(s) {
    menuChat = ''
    renameChat = s.chat_id
    renameText = s.title || s.preview || ''
  }
  async function commitRename(s) {
    if (renameChat !== s.chat_id) return // Escape already cancelled; ignore the blur
    renameChat = ''
    const t = renameText.trim()
    if (!t || t === (s.title || '')) return
    const prev = s.title
    $chats = $chats.map((c) => (c.chat_id === s.chat_id ? { ...c, title: t } : c))
    try { await api.updateChat(s.chat_id, { title: t }) } catch {
      $chats = $chats.map((c) => (c.chat_id === s.chat_id ? { ...c, title: prev } : c))
    }
  }
  function focusSelect(node) { node.focus(); node.select() }

  // status → Lucide icon name + tooltip label. Colored per-status via the
  // .statusicon CSS classes; replaces the old emoji/unicode glyphs.
  const STATUS = {
    pending: { icon: 'clock', label: 'pending' },
    scheduled: { icon: 'clock', label: 'scheduled' },
    awaiting_input: { icon: 'message', label: 'needs input' },
    planning: { icon: 'brain', label: 'planning' },
    running: { icon: 'zap', label: 'running' },
    completed: { icon: 'check', label: 'completed' },
    failed: { icon: 'x', label: 'failed' },
    cancelled: { icon: 'x', label: 'cancelled' },
  }
  const stat = (s) => STATUS[s] || { icon: 'clock', label: s || '' }

  const TERMINAL = new Set(['completed', 'failed', 'cancelled'])
  const isUnread = (t) => TERMINAL.has(t.status) && !t.seen  // a finished result not yet opened

  // Top-level ordering: what's active now first, then upcoming scheduled tasks by
  // soonest next run, then finished (completed/failed/cancelled) at the bottom.
  function taskRank(t) {
    if (TERMINAL.has(t.status)) return 2 // finished → bottom
    if (t.status === 'scheduled') return 1 // upcoming → middle, ordered by next run
    return 0 // running / pending / planning / awaiting input → active now, at top
  }
  function compareTasks(a, b) {
    const ra = taskRank(a), rb = taskRank(b)
    if (ra !== rb) return ra - rb
    if (ra === 1) // scheduled: soonest next run first
      return (a.scheduled_for || '').localeCompare(b.scheduled_for || '')
    // active-now and finished: most recent first
    return (b.created_at || '').localeCompare(a.created_at || '')
  }

  // Group runs (run_of) under their template; templates + standalone tasks are
  // top-level. Orphan runs (template absent) fall back to top level.
  const groups = $derived.by(() => {
    const list = $tasks || []
    const byParent = new Map()
    for (const t of list) {
      if (!t.run_of) continue
      const arr = byParent.get(t.run_of)
      if (arr) arr.push(t); else byParent.set(t.run_of, [t])
    }
    const topIds = new Set(list.filter((t) => !t.run_of).map((t) => t.id))
    const tops = list.filter((t) => !t.run_of || !topIds.has(t.run_of))
    return tops
      .slice()
      .sort(compareTasks)
      .map((t) => {
        const runs = (byParent.get(t.id) || []).slice().sort((a, b) =>
          (b.scheduled_for || b.created_at || '').localeCompare(a.scheduled_for || a.created_at || ''))
        return { task: t, runs, unread: runs.filter(isUnread).length }
      })
  })

  // A caught-up recurring task collapses to just its header (title + recurrence +
  // next run) — the seen ✓ history is noise once read, and the full run list is
  // always available in the task's detail panel. So in the sidebar we only keep
  // runs that still want attention: an unread result, a run still in flight, or
  // whichever run is open right now (opening a run marks it seen; without this it
  // would vanish from under its parent the instant you clicked it).
  const needsAttention = (r, openId) =>
    isUnread(r) || !TERMINAL.has(r.status) || r.id === openId
  const visibleRuns = (g, openId) => g.runs.filter((r) => needsAttention(r, openId))

</script>

<div class="drawer">
  <div class="dhead">
    <img class="brandlogo on-light" src={ag2Logo} alt="AG2" />
    <img class="brandlogo on-dark" src={ag2LogoWhite} alt="AG2" />
    <span class="brand">Assistant</span>
  </div>

  {#if active}
    <div class="profchips">
      <div class="chiprow" role="tablist" aria-label="Profiles">
        {#each inlineChips as p (p.id)}
          {@const isActive = p.id === active.id}
          <button
            class="chip"
            class:active={isActive}
            style="--p:{p.accent}; --p-ink:{inkOn(p.accent)}"
            title={chipTitle(p)}
            role="tab"
            aria-selected={isActive}
            aria-current={isActive ? 'true' : undefined}
            onclick={() => (isActive ? null : switchTo(p.id))}
          >
            <span class="mono">{initial(p)}</span>
            {#if hasUnseen(p.id)}<span class="actdot" title="unread results"></span>{/if}
          </button>
        {/each}

        {#if overflow.length}
          {@const overflowActive = overflow.some((p) => p.id === active.id)}
          {@const overflowUnseen = overflow.some((p) => hasUnseen(p.id))}
          <button
            class="chip more"
            class:active={overflowActive}
            title="More profiles"
            aria-haspopup="menu"
            aria-expanded={pickerOpen}
            onclick={() => (pickerOpen = !pickerOpen)}
          >
            <span class="mono">+{overflow.length}</span>
            {#if overflowUnseen}<span class="actdot" title="unread results"></span>{/if}
          </button>
        {/if}

        <!-- "+" chip → create-profile modal (§5.4). -->
        <button class="chip add" title="New profile" aria-label="New profile" onclick={() => (createOpen = true)}>
          <Icon name="plus" size={15} />
        </button>
      </div>

      <span class="activename" title={active.name}>{active.name}</span>

      {#if pickerOpen && overflow.length}
        <div class="profmenu" role="menu">
          {#each overflow as p (p.id)}
            {@const isActive = p.id === active.id}
            <button
              class="profitem"
              class:active={isActive}
              role="menuitem"
              title={chipTitle(p)}
              aria-current={isActive ? 'true' : undefined}
              onclick={() => (isActive ? (pickerOpen = false) : switchTo(p.id))}
            >
              <span class="profdot" style="--dot:{p.accent}"></span>
              <span class="profname">{p.name}</span>
              {#if hasUnseen(p.id)}<span class="actdot inmenu" title="unread results"></span>{/if}
              {#if isActive}<Icon name="check" size={13} />{/if}
            </button>
          {/each}
        </div>
      {/if}
    </div>
  {/if}

  {#if createOpen}
    <div class="modal-backdrop" onclick={() => (createOpen = false)}></div>
    <div class="modal profcreate">
      <h2>New profile</h2>
      <p class="pc-lead">A colour-coded, isolated workspace — its own chats, tasks, memory, and files.</p>
      <ProfileForm claimed={claimedAccents} submitLabel="Create profile" busyLabel="Creating…" onSubmit={createProfile} />
      <button class="modal-close" onclick={() => (createOpen = false)}>Cancel</button>
    </div>
  {/if}

  <div class="segbar" role="tablist" aria-label="View">
    <button class="seg" class:on={$drawerTab === 'chats'} role="tab" aria-selected={$drawerTab === 'chats'} onclick={() => ($drawerTab = 'chats')}><Icon name="message" size={14} /> Chats</button>
    <button class="seg" class:on={$drawerTab === 'tasks'} role="tab" aria-selected={$drawerTab === 'tasks'} onclick={() => ($drawerTab = 'tasks')}><Icon name="list" size={14} /> Tasks</button>
  </div>

  {#snippet chatRow(s)}
    <div class="drow chatrow" class:on={$route.name === 'chat' && $route.id === s.chat_id} onclick={() => openChat(s.chat_id)}>
      {#if renameChat === s.chat_id}
        <input class="renamein" value={renameText} use:focusSelect
          oninput={(e) => (renameText = e.target.value)}
          onclick={(e) => e.stopPropagation()}
          onkeydown={(e) => { if (e.key === 'Enter') commitRename(s); else if (e.key === 'Escape') renameChat = '' }}
          onblur={() => commitRename(s)} />
      {:else}
        <div class="clabel" title={s.preview || ''}>{s.title || s.preview || s.chat_id}</div>
      {/if}
      {#if confirmChat === s.chat_id}
        <span class="rowconfirm" onclick={(e) => e.stopPropagation()}>
          <span class="confirm">Delete?</span>
          <button class="linkbtn danger" disabled={busyChat === s.chat_id}
            onclick={(e) => { e.stopPropagation(); delChat(s.chat_id) }}>{busyChat === s.chat_id ? '…' : 'yes'}</button>
          <button class="linkbtn" onclick={(e) => { e.stopPropagation(); confirmChat = '' }}>no</button>
        </span>
      {:else if renameChat !== s.chat_id}
        {#if s.updated}<span class="rowtime">{fmtAgoShort(s.updated)}</span>{/if}
        <button class="rowkebab" title="Chat actions" aria-haspopup="menu" aria-expanded={menuChat === s.chat_id}
          onclick={(e) => toggleMenu(e, s)}><Icon name="ellipsis-vertical" size={14} /></button>
        {#if menuChat === s.chat_id}
          <div class="chatmenu" role="menu" tabindex="-1" style="left:{menuPos.x}px; top:{menuPos.y}px"
            onclick={(e) => e.stopPropagation()}>
            <button class="cmitem" role="menuitem" onclick={() => toggleStar(s)}>
              <Icon name="star" size={14} /> {s.starred ? 'Unstar' : 'Star'}
            </button>
            <button class="cmitem" role="menuitem" onclick={() => startRename(s)}>
              <Icon name="pencil" size={14} /> Rename
            </button>
            <div class="cmdiv"></div>
            <button class="cmitem danger" role="menuitem"
              onclick={() => { menuChat = ''; confirmChat = s.chat_id }}>
              <Icon name="trash" size={14} /> Delete
            </button>
          </div>
        {/if}
      {/if}
    </div>
  {/snippet}

  <div class="dlist" onscroll={() => (menuChat = '')}>
    {#if $drawerTab === 'chats'}
      <button class="newrow" onclick={newChat}><Icon name="plus" size={15} /> New chat</button>
      {#if !$chats.length}<div class="none">No conversations yet.</div>{/if}
      {#if starredChats.length}
        <div class="datesep">Starred</div>
        {#each starredChats as s (s.chat_id)}{@render chatRow(s)}{/each}
      {/if}
      {#each chatRows as { item: s, sep } (s.chat_id)}
        {#if sep}<div class="datesep">{sep}</div>{/if}
        {@render chatRow(s)}
      {/each}
    {:else}
      {#if !groups.length}<div class="none">No tasks yet.</div>{/if}
      {#each groups as g (g.task.id)}
        {@const nextIn = g.task.status === 'scheduled' ? fmtNextIn(g.task.scheduled_for) : ''}
        {@const openId = $route.name === 'task' ? $route.id : null}
        {@const shownRuns = visibleRuns(g, openId)}
        <div class="drow ttask" class:on={$route.name === 'task' && $route.id === g.task.id}
             class:unseen={!g.runs.length && isUnread(g.task)} onclick={() => openTask(g.task.id)}>
          <div class="tline1">
            <span class="statusicon {g.task.status}" title={stat(g.task.status).label}><Icon name={stat(g.task.status).icon} size={14} /></span>
            <span class="ttitle">{g.task.title}</span>
            {#if g.unread}<span class="unreadcount" title="{g.unread} unread">{g.unread}</span>{/if}
          </div>
          {#if g.task.recurrence || nextIn}
            <div class="tmeta">
              {#if g.task.recurrence}<span class="tag sched" title="{g.task.recurrence}{g.task.recurrence_desc ? ' — ' + g.task.recurrence_desc : ''}">{shortSched(g.task.recurrence_desc) || g.task.recurrence}</span>{/if}
              {#if nextIn}<span class="nextin" title="Next run {fmtWhen(g.task.scheduled_for)}">{nextIn}</span>{/if}
            </div>
          {/if}
        </div>
        {#each shownRuns as r (r.id)}
          <div class="drow child trow" class:on={$route.name === 'task' && $route.id === r.id}
               class:unseen={isUnread(r)} onclick={() => openTask(r.id)}>
            <span class="statusicon {r.status}" title={stat(r.status).label}><Icon name={stat(r.status).icon} size={13} /></span>
            <span class="runwhen">{fmtWhen(r.scheduled_for || r.created_at) || 'run'}</span>
            {#if isUnread(r)}<span class="dot" title="unread"></span>{/if}
          </div>
        {/each}
      {/each}
    {/if}
  </div>

  {#if usageLabel}
    <div class="usagehud" title={usageTitle}>
      <span class="uhicon"><Icon name="cpu" size={13} /></span><span class="uhlabel">Today · {usageLabel}</span>
    </div>
  {/if}
  <div class="dfoot">
    <button class="settingsbtn" onclick={() => ($filesOpen = true)}><Icon name="folder" size={15} /> Files</button>
    <button class="settingsbtn" onclick={() => ($settingsOpen = true)}><Icon name="settings" size={15} /> Settings</button>
  </div>
</div>

<style>
  /* Profile switcher chips (§5.4). A row of palette-tinted monogram chips below
     the brand header — the active profile filled with its palette colour, others
     outlined in it. The active profile's name sits beside the row (kept visible
     by request). >4 profiles fold the overflow into a "+N" chip + anchored menu
     (reusing .profmenu/.profitem below). Supersedes the Phase-1 indicator. */
  .profchips {
    position: relative;
    display: flex; align-items: center; gap: 10px;
    padding: 10px 16px;
    min-width: 0;
  }
  .chiprow { display: flex; align-items: center; gap: 6px; flex: none; }
  .chip {
    position: relative; flex: none;
    width: 28px; height: 28px; border-radius: var(--radius-pill);
    display: inline-flex; align-items: center; justify-content: center;
    cursor: pointer; font: inherit; font-size: var(--text-xs);
    font-weight: var(--fw-bold); line-height: 1;
    /* outlined by default: tinted ring + tinted glyph on the surface */
    background: var(--surface);
    color: var(--p, var(--accent));
    border: 1.5px solid var(--p, var(--accent));
    transition: transform var(--dur-fast) var(--ease-out), box-shadow var(--dur-fast) var(--ease-out);
  }
  .chip:hover { transform: translateY(-1px); }
  .chip:focus-visible { outline: none; box-shadow: 0 0 0 3px color-mix(in srgb, var(--p, var(--accent)) 35%, transparent); }
  /* active: filled with the palette colour, white glyph, cursor default. The selected
     ring is INSET (a surface-coloured gap just inside the tinted border) so the chip
     keeps the exact same 28px footprint as the others — an OUTER ring would extend past
     the box and make the row's spacing/alignment shift per active-chip position. */
  .chip.active {
    background: var(--p, var(--accent)); color: var(--p-ink, var(--text-on-accent)); cursor: default;
    border-color: var(--p, var(--accent));
    box-shadow: inset 0 0 0 2px var(--surface);
  }
  .chip.more { color: var(--muted); border-color: var(--line); background: var(--surface); font-weight: var(--fw-semibold); }
  .chip.more:hover { color: var(--text); border-color: var(--text); }
  .chip.more.active { color: var(--text); }
  /* "+" chip: a quiet dashed affordance to add a profile */
  .chip.add { color: var(--muted); border-style: dashed; border-color: var(--line); background: var(--surface); }
  .chip.add:hover { color: var(--text); border-color: var(--text); }
  .mono { pointer-events: none; }
  /* unread-results dot: a small badge in the chip's top-right when the profile has
     finished tasks the user hasn't opened yet. A fixed amber attention color (not the
     palette --accent) so it reads as "new" and stays legible on every profile tint. */
  .actdot {
    position: absolute; top: -1px; right: -1px;
    width: 8px; height: 8px; border-radius: var(--radius-pill);
    background: var(--warning);
    border: 1.5px solid var(--surface);
  }
  .actdot.inmenu { position: static; margin-left: auto; border-color: var(--surface); }

  .activename {
    min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-size: var(--text-sm); font-weight: var(--fw-semibold); color: var(--text);
  }

  .profdot {
    width: 8px; height: 8px; flex: none; border-radius: var(--radius-pill);
    background: var(--dot, var(--accent));
  }
  .profname {
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-weight: var(--fw-medium);
  }

  /* Overflow picker (>4 profiles). Absolutely positioned under the chip row,
     styled with existing tokens to match the drawer aesthetic. */
  .profmenu {
    position: absolute; top: calc(100% - 4px); left: 16px; z-index: var(--z-modal);
    min-width: 150px; max-width: 220px; display: flex; flex-direction: column;
    padding: 4px; background: var(--surface-elevated); border: 1px solid var(--line);
    border-radius: var(--radius-sm); box-shadow: var(--shadow-lg);
  }
  .profitem {
    display: flex; align-items: center; gap: 8px; width: 100%;
    padding: 6px 8px; cursor: pointer; text-align: left;
    border: none; background: none; font: inherit; font-size: var(--text-xs);
    color: var(--text); border-radius: var(--radius-xs, 6px);
    transition: background var(--dur-fast) var(--ease-out);
  }
  .profitem:hover { background: var(--surface-hover); }
  .profitem .profname { flex: 1; font-weight: var(--fw-medium); }
  .profitem.active { color: var(--muted); cursor: default; }
  .profitem.active:hover { background: none; }
  .profitem :global(svg) { flex: none; opacity: .7; }

  /* Create-profile modal: uses the global .modal shell; just a lead line here. */
  .profcreate { width: min(460px, 92vw); }
  .pc-lead { font-size: var(--text-sm); color: var(--text-muted); line-height: var(--leading-normal); margin: -2px 0 6px; }

  /* Chat row: title + a hover-revealed kebab (⋮) that opens a Star/Rename/Delete
     menu. Delete reuses the inline "Delete?" confirm (same idiom as the Files
     modal) and is permanent. */
  /* Date section header between chat rows: last-message day, left-aligned and
     muted so it frames the group without competing with the chat titles. The
     first-child rule drops the top margin so "Recent" hugs the list top. */
  .datesep { font-size: 11px; font-weight: 600; letter-spacing: .3px; color: var(--muted); text-transform: uppercase; padding: 2px 8px; margin: 10px 0 2px; }
  .datesep:first-child { margin-top: 2px; }
  .chatrow { display: flex; align-items: center; gap: 8px; }
  .clabel { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  /* Last-activity stamp, right-aligned; the hover swaps it for the delete
     affordance so the row's right slot never doubles up. */
  .rowtime { flex: none; font-size: 11px; color: var(--text-faint); font-variant-numeric: tabular-nums; white-space: nowrap; }
  .chatrow:hover .rowtime, .chatrow:focus-within .rowtime { display: none; }
  .rowconfirm { flex: none; display: inline-flex; align-items: center; gap: 7px; }
  .rowconfirm .confirm { color: #d8552f; font-size: 12px; }
  .rowconfirm .linkbtn { border: none; background: none; font: inherit; font-size: 12px; cursor: pointer; padding: 0; color: var(--accent); }
  .rowconfirm .linkbtn:hover { text-decoration: underline; }
  .rowconfirm .linkbtn.danger { color: var(--muted); }
  .rowconfirm .linkbtn.danger:hover { color: #d8552f; }
  .rowconfirm .linkbtn.danger:disabled { cursor: default; opacity: .6; }

  /* Kebab: hover-revealed like the old trash; swaps with the timestamp. */
  .rowkebab { flex: none; display: inline-flex; align-items: center; justify-content: center; padding: 2px; border: none; background: none; color: var(--muted); cursor: pointer; border-radius: 6px; opacity: 0; width: 0; overflow: hidden; transition: opacity var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out); }
  .chatrow:hover .rowkebab, .chatrow:focus-within .rowkebab { opacity: .55; width: auto; }
  .rowkebab:hover { opacity: 1; color: var(--text); }

  /* Row action menu: fixed-position (escapes the scrolling list), right edge
     anchored to the kebab via translateX(-100%). */
  .chatmenu { position: fixed; z-index: var(--z-modal); transform: translateX(-100%); min-width: 150px; display: flex; flex-direction: column; padding: 4px; background: var(--surface-elevated); border: 1px solid var(--line); border-radius: var(--radius-sm); box-shadow: var(--shadow-lg); }
  .cmitem { display: flex; align-items: center; gap: 8px; width: 100%; padding: 6px 8px; border: none; background: none; font: inherit; font-size: var(--text-xs); color: var(--text); border-radius: var(--radius-xs, 6px); cursor: pointer; text-align: left; }
  .cmitem:hover { background: var(--surface-hover); }
  .cmitem.danger { color: var(--danger, #d8552f); }
  .cmitem.danger:hover { background: color-mix(in srgb, var(--danger, #d8552f) 12%, transparent); }
  .cmitem :global(svg) { flex: none; opacity: .7; }
  .cmdiv { height: 1px; margin: 4px 6px; background: var(--line); }

  /* Inline rename input, replacing the label at the row's own size. */
  .renamein { flex: 1; min-width: 0; font: inherit; font-size: inherit; color: var(--text); background: var(--surface); border: 1px solid var(--accent); border-radius: 6px; padding: 1px 6px; outline: none; }
</style>
