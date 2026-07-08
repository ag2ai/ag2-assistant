<script>
  import { onMount } from 'svelte'
  import { sessions, tasks, drawerTab, settingsOpen, filesOpen, profiles } from '../store.js'
  import { route, go, newChatId } from '../router.js'
  import { api } from '../transport/api.js'
  import { PALETTES } from '../design/palette.js'
  import Icon from './Icon.svelte'
  import ProfileForm from './ProfileForm.svelte'
  import { fmtWhen, fmtNextIn } from '../lib/time.js'
  import ag2Logo from '../assets/ag2.svg'
  import ag2LogoWhite from '../assets/ag2-white.svg'

  // Profile switcher chips (§5.4). A row of palette-tinted monogram chips at the
  // top of the Drawer: the active profile filled, others outlined; click switches
  // (full-page nav — App.svelte's boot makes the URL pid the persisted choice and
  // applies its palette). >4 profiles collapse the overflow into a small anchored
  // picker. Reuses the PALETTES hex map (same source as the create-profile
  // swatches) — profile.palette id → --p-500 hex.
  const paletteHex = (id) => (PALETTES.find((p) => p.id === id) || {}).hex
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
  }
  function onDocKey(e) {
    if (createOpen && e.key === 'Escape') { createOpen = false; return }
    if (pickerOpen && e.key === 'Escape') pickerOpen = false
  }

  // "+" chip → profile-creation modal (§5.4). Reuses ProfileForm (same form as
  // onboarding). Palettes already claimed by existing profiles are hidden. On
  // success → full-page navigate to /app/{pid}/ (App.svelte's boot adopts it and
  // applies its palette). 400s (e.g. duplicate palette) surface inline in the form.
  let createOpen = $state(false)
  const claimedPalettes = $derived(list.map((p) => p.palette))
  async function createProfile({ name, palette, workspace }) {
    const res = await api.createProfile(name, palette, workspace) // throws → inline
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
      const server = await api.sessions()
      const ids = new Set(server.map((s) => s.session_id))
      // keep optimistic, not-yet-persisted chats (just sent, agent still replying)
      $sessions = [...$sessions.filter((s) => !ids.has(s.session_id)), ...server]
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

  const openChat = (id) => go('/c/' + id)
  const openTask = (id) => go('/t/' + id)
  const newChat = () => go('/c/' + newChatId())

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
            style="--p:{paletteHex(p.palette)}"
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
              <span class="profdot" style="--dot:{paletteHex(p.palette)}"></span>
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
      <ProfileForm claimed={claimedPalettes} submitLabel="Create profile" busyLabel="Creating…" onSubmit={createProfile} />
      <button class="modal-close" onclick={() => (createOpen = false)}>Cancel</button>
    </div>
  {/if}

  <div class="tabs">
    <button class="tab" class:on={$drawerTab === 'chats'} onclick={() => ($drawerTab = 'chats')}><Icon name="message" size={13} /> Chats</button>
    <button class="tab" class:on={$drawerTab === 'tasks'} onclick={() => ($drawerTab = 'tasks')}><Icon name="list" size={13} /> Tasks</button>
    <button class="newbtn" onclick={newChat}><Icon name="plus" size={14} /> New</button>
  </div>

  <div class="dlist">
    {#if $drawerTab === 'chats'}
      {#if !$sessions.length}<div class="none">No conversations yet.</div>{/if}
      {#each $sessions as s (s.session_id)}
        <div class="drow" class:on={$route.name === 'chat' && $route.id === s.session_id} onclick={() => openChat(s.session_id)}>
          <div title={s.preview || ''}>{s.title || s.preview || s.session_id}</div>
        </div>
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
              {#if g.task.recurrence}<span class="tag sched" title="repeats {g.task.recurrence}">{g.task.recurrence}</span>{/if}
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
  /* active: filled with the palette colour, white glyph, cursor default */
  .chip.active {
    background: var(--p, var(--accent)); color: #fff; cursor: default;
    box-shadow: 0 0 0 2px var(--surface), 0 0 0 3.5px var(--p, var(--accent));
  }
  .chip.more { color: var(--muted); border-color: var(--line); background: var(--surface); font-weight: var(--fw-semibold); }
  .chip.more:hover { color: var(--text); border-color: var(--text); }
  .chip.more.active { color: var(--text); }
  /* "+" chip: a quiet dashed affordance to add a profile */
  .chip.add { color: var(--muted); border-style: dashed; border-color: var(--line); background: var(--surface); }
  .chip.add:hover { color: var(--accent); border-color: var(--accent); }
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
    padding: 4px; background: var(--surface); border: 1px solid var(--line);
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
</style>
