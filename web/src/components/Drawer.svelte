<script>
  import { onMount } from 'svelte'
  import { sessions, tasks, drawerTab, settingsOpen, filesOpen, profiles } from '../store.js'
  import { route, go, newChatId } from '../router.js'
  import { api } from '../transport/api.js'
  import { PALETTES } from '../design/palette.js'
  import Icon from './Icon.svelte'
  import { fmtWhen, fmtNextIn } from '../lib/time.js'
  import ag2Logo from '../assets/ag2.svg'
  import ag2LogoWhite from '../assets/ag2-white.svg'

  // Active-profile indicator + switcher (§7 Phase 1). Name + palette dot, and —
  // when there's more than one profile — a way to switch. Resolve the active
  // profile from the `profiles` store and map its palette id to the hex the
  // create form already uses (reuse PALETTES, don't duplicate).
  const paletteHex = (id) => (PALETTES.find((p) => p.id === id) || {}).hex
  const list = $derived($profiles.list || [])
  const active = $derived(list.find((p) => p.id === $profiles.activeId))
  const others = $derived(list.filter((p) => p.id !== $profiles.activeId))

  // Phase-1 switch is a full page load to /app/{pid}/ — App.svelte's boot
  // resolves the URL pid and makes it the persisted choice (no hot-switching).
  const switchTo = (pid) => location.assign('/app/' + pid + '/')

  // 1 profile → non-interactive label. 2 → click alternates to the other.
  // 3+ → click opens a small anchored picker.
  const canSwitch = $derived(list.length >= 2)
  let pickerOpen = $state(false)
  function onIndicator() {
    if (list.length === 2) switchTo(others[0].id)     // straight to the other
    else if (list.length >= 3) pickerOpen = !pickerOpen // small picker
  }
  const indicatorTitle = $derived(
    list.length === 2 ? 'Switch to ' + (others[0]?.name || 'profile')
      : list.length >= 3 ? 'Switch profile'
      : 'Active profile: ' + (active?.name || '')
  )
  // Close the picker on click-outside / Escape.
  function onDocPointer(e) {
    if (pickerOpen && !e.target.closest('.profswitch')) pickerOpen = false
  }
  function onDocKey(e) { if (pickerOpen && e.key === 'Escape') pickerOpen = false }

  let usage = $state(null)   // today's token/cost totals for the activity HUD

  async function refresh() {
    try {
      const server = await api.sessions()
      const ids = new Set(server.map((s) => s.session_id))
      // keep optimistic, not-yet-persisted chats (just sent, agent still replying)
      $sessions = [...$sessions.filter((s) => !ids.has(s.session_id)), ...server]
    } catch {}
    try { $tasks = await api.tasksAll('all') } catch {}
    try { usage = await api.usage() } catch {}
  }

  const fmtTok = (n) =>
    !n ? '0' : n < 1000 ? String(Math.round(n)) : `${(n / 1000).toFixed(n < 10000 ? 1 : 0)}k`
  // Cost is an estimate (~) and only shown when the model(s) used had known pricing.
  const usageLabel = $derived.by(() => {
    if (!usage || !usage.total) return ''
    const tok = `${fmtTok(usage.total)} tok`
    return usage.priced ? `${tok} · ~$${usage.cost.toFixed(usage.cost < 1 ? 3 : 2)}` : tok
  })
  const usageTitle = $derived.by(() => {
    if (!usage) return ''
    const models = Object.keys(usage.by_model || {}).join(', ')
    return `Today (${usage.date}): ${fmtTok(usage.prompt)} in / ${fmtTok(usage.completion)} out`
      + (usage.priced ? ` · ~$${(usage.cost || 0).toFixed(4)} (estimate)` : ' · cost: no price set')
      + (models ? `\nmodels: ${models}` : '')
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

  const RECENT = 5
  let expanded = $state(new Set())   // template ids showing all their runs
  function toggle(id) {
    const n = new Set(expanded)
    n.has(id) ? n.delete(id) : n.add(id)
    expanded = n
  }

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

  // recent N runs, but always include unread ones even past the cap
  function visibleRuns(g) {
    if (expanded.has(g.task.id)) return g.runs
    const recent = g.runs.slice(0, RECENT)
    return [...recent, ...g.runs.slice(RECENT).filter(isUnread)]
  }

</script>

<div class="drawer">
  <div class="dhead">
    <img class="brandlogo on-light" src={ag2Logo} alt="AG2" />
    <img class="brandlogo on-dark" src={ag2LogoWhite} alt="AG2" />
    <span class="brand">Assistant</span>
    {#if active}
      <div class="profswitch">
        {#if canSwitch}
          <button
            class="profind"
            title={indicatorTitle}
            aria-haspopup={list.length >= 3 ? 'menu' : undefined}
            aria-expanded={list.length >= 3 ? pickerOpen : undefined}
            onclick={onIndicator}
          >
            <span class="profdot" style="--dot:{paletteHex(active.palette)}"></span>
            <span class="profname">{active.name}</span>
            <Icon name="chevron-down" size={12} />
          </button>
        {:else}
          <span class="profind static" title={indicatorTitle}>
            <span class="profdot" style="--dot:{paletteHex(active.palette)}"></span>
            <span class="profname">{active.name}</span>
          </span>
        {/if}

        {#if pickerOpen && list.length >= 3}
          <div class="profmenu" role="menu">
            {#each list as p (p.id)}
              {@const isActive = p.id === active.id}
              <button
                class="profitem"
                class:active={isActive}
                role="menuitem"
                aria-current={isActive ? 'true' : undefined}
                onclick={() => (isActive ? (pickerOpen = false) : switchTo(p.id))}
              >
                <span class="profdot" style="--dot:{paletteHex(p.palette)}"></span>
                <span class="profname">{p.name}</span>
                {#if isActive}<Icon name="check" size={13} />{/if}
              </button>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>
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
        <div class="drow ttask" class:on={$route.name === 'task' && $route.id === g.task.id}
             class:unseen={!g.runs.length && isUnread(g.task)} onclick={() => openTask(g.task.id)}>
          <div class="tline1">
            <span class="statusicon {g.task.status}" title={stat(g.task.status).label}><Icon name={stat(g.task.status).icon} size={14} /></span>
            <span class="ttitle">{g.task.title}</span>
            {#if g.unread}<span class="unreadcount" title="{g.unread} unread">{g.unread}</span>{/if}
          </div>
          {#if g.task.recurrence || nextIn}
            <div class="tmeta">
              {#if g.task.recurrence}<span class="tag sched" title="repeats {g.task.recurrence}"><Icon name="clock" size={11} /> {g.task.recurrence}</span>{/if}
              {#if nextIn}<span class="nextin" title="Next run {fmtWhen(g.task.scheduled_for)}">{nextIn}</span>{/if}
            </div>
          {/if}
        </div>
        {#each visibleRuns(g) as r (r.id)}
          <div class="drow child trow" class:on={$route.name === 'task' && $route.id === r.id}
               class:unseen={isUnread(r)} onclick={() => openTask(r.id)}>
            <span class="statusicon {r.status}" title={stat(r.status).label}><Icon name={stat(r.status).icon} size={13} /></span>
            <span class="runwhen">{fmtWhen(r.scheduled_for || r.created_at) || 'run'}</span>
            {#if isUnread(r)}<span class="dot" title="unread"></span>{/if}
          </div>
        {/each}
        {#if g.runs.length > visibleRuns(g).length}
          <button class="showall" onclick={() => toggle(g.task.id)}>… show all {g.runs.length}</button>
        {:else if expanded.has(g.task.id) && g.runs.length > RECENT}
          <button class="showall" onclick={() => toggle(g.task.id)}>show fewer</button>
        {/if}
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
  /* Active-profile indicator + switcher. Sits at the end of the header row,
     marking which profile the client is viewing. With 1 profile it's a plain
     label; with 2+ it becomes a small quiet switcher (alternate at 2, picker
     at 3+). A utility control, not a modal — small and understated.
     Phase 2's §5.4 chips supersede this. */
  .profswitch { margin-left: auto; position: relative; min-width: 0; }
  .profind {
    display: inline-flex; align-items: center; gap: 6px; max-width: 100%;
    min-width: 0; font-size: var(--text-xs); color: var(--muted);
  }
  /* button variant: quiet by default, faint hover affordance */
  button.profind {
    cursor: pointer; border: 1px solid transparent; background: none;
    font: inherit; font-size: var(--text-xs); color: var(--muted);
    padding: 3px 6px; border-radius: var(--radius-sm);
    transition: background var(--dur-fast) var(--ease-out), border-color var(--dur-fast) var(--ease-out);
  }
  button.profind:hover { background: var(--surface-hover); border-color: var(--line); color: var(--text); }
  button.profind:focus-visible { outline: none; box-shadow: var(--focus-ring); }
  .profdot {
    width: 8px; height: 8px; flex: none; border-radius: var(--radius-pill);
    background: var(--dot, var(--accent));
  }
  .profname {
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    font-weight: var(--fw-medium);
  }

  /* Small anchored picker (3+ profiles). Absolutely positioned under the
     indicator, styled with existing tokens to match the drawer aesthetic. */
  .profmenu {
    position: absolute; top: calc(100% + 4px); right: 0; z-index: var(--z-modal);
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
</style>
