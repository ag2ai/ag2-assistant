<script>
  import { onMount } from 'svelte'
  import { sessions, tasks, drawerTab, settingsOpen, filesOpen } from '../store.js'
  import { route, go, newChatId } from '../router.js'
  import { api } from '../transport/api.js'
  import logo from '../assets/ag2-logo.png'

  async function refresh() {
    try {
      const server = await api.sessions()
      const ids = new Set(server.map((s) => s.session_id))
      // keep optimistic, not-yet-persisted chats (just sent, agent still replying)
      $sessions = [...$sessions.filter((s) => !ids.has(s.session_id)), ...server]
    } catch {}
    try { $tasks = await api.tasksAll('all') } catch {}
  }
  onMount(() => { refresh(); const t = setInterval(refresh, 5000); return () => clearInterval(t) })

  const openChat = (id) => go('/c/' + id)
  const openTask = (id) => go('/t/' + id)
  const newChat = () => go('/c/' + newChatId())

  // status → icon + tooltip label (replaces the old text badges/tags)
  const STATUS = {
    pending: { icon: '○', label: 'pending' },
    scheduled: { icon: '⏰', label: 'scheduled' },
    awaiting_input: { icon: '✋', label: 'needs input' },
    planning: { icon: '✎', label: 'planning' },
    running: { icon: '●', label: 'running' },
    completed: { icon: '✓', label: 'completed' },
    failed: { icon: '⚠', label: 'failed' },
    cancelled: { icon: '⊘', label: 'cancelled' },
  }
  const stat = (s) => STATUS[s] || { icon: '•', label: s || '' }

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

  function fmtWhen(iso) {
    if (!iso) return ''
    try { return new Date(iso).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' }) }
    catch { return '' }
  }

  // Relative time until the next scheduled run, e.g. "Next in 3 mins", "Next in
  // 1 day", "Next in 4 weeks". Recomputed every drawer refresh (5s) so it ticks down.
  const _plural = (n, unit) => `${n} ${unit}${n === 1 ? '' : 's'}`
  function fmtNextIn(iso) {
    if (!iso) return ''
    const ms = new Date(iso).getTime() - Date.now()
    if (isNaN(ms)) return ''
    if (ms <= 0) return 'Due now'
    const mins = Math.round(ms / 60000)
    if (mins < 1) return 'Next in <1 min'
    if (mins < 60) return `Next in ${_plural(mins, 'min')}`
    const hours = Math.round(mins / 60)
    if (hours < 24) return `Next in ${_plural(hours, 'hour')}`
    const days = Math.round(hours / 24)
    if (days < 7) return `Next in ${_plural(days, 'day')}`
    const weeks = Math.round(days / 7)
    if (weeks < 5) return `Next in ${_plural(weeks, 'week')}`
    return `Next in ${_plural(Math.round(days / 30), 'month')}`
  }
</script>

<div class="drawer">
  <div class="dhead">
    <img class="brandlogo" src={logo} alt="AG2" />
    <span class="brand">Assistant</span>
  </div>
  <div class="tabs">
    <button class="tab" class:on={$drawerTab === 'chats'} onclick={() => ($drawerTab = 'chats')}>Chats</button>
    <button class="tab" class:on={$drawerTab === 'tasks'} onclick={() => ($drawerTab = 'tasks')}>Tasks</button>
    <button class="newbtn" onclick={newChat}>+ New</button>
  </div>

  <div class="dlist">
    {#if $drawerTab === 'chats'}
      {#if !$sessions.length}<div class="none">No conversations yet.</div>{/if}
      {#each $sessions as s (s.session_id)}
        <div class="drow" class:on={$route.name === 'chat' && $route.id === s.session_id} onclick={() => openChat(s.session_id)}>
          <div>{s.preview || s.session_id}</div>
        </div>
      {/each}
    {:else}
      {#if !groups.length}<div class="none">No tasks yet.</div>{/if}
      {#each groups as g (g.task.id)}
        {@const nextIn = g.task.status === 'scheduled' ? fmtNextIn(g.task.scheduled_for) : ''}
        <div class="drow ttask" class:on={$route.name === 'task' && $route.id === g.task.id}
             class:unseen={!g.runs.length && isUnread(g.task)} onclick={() => openTask(g.task.id)}>
          <div class="tline1">
            <span class="statusicon {g.task.status}" title={stat(g.task.status).label}>{stat(g.task.status).icon}</span>
            <span class="ttitle">{g.task.title}</span>
            {#if g.unread}<span class="unreadcount" title="{g.unread} unread">{g.unread}</span>{/if}
          </div>
          {#if g.task.recurrence || nextIn}
            <div class="tmeta">
              {#if g.task.recurrence}<span class="tag sched" title="repeats {g.task.recurrence}">🔁 {g.task.recurrence}</span>{/if}
              {#if nextIn}<span class="nextin" title="Next run {fmtWhen(g.task.scheduled_for)}">{nextIn}</span>{/if}
            </div>
          {/if}
        </div>
        {#each visibleRuns(g) as r (r.id)}
          <div class="drow child trow" class:on={$route.name === 'task' && $route.id === r.id}
               class:unseen={isUnread(r)} onclick={() => openTask(r.id)}>
            <span class="statusicon {r.status}" title={stat(r.status).label}>{stat(r.status).icon}</span>
            <span class="runwhen">↻ {fmtWhen(r.scheduled_for || r.created_at) || 'run'}</span>
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

  <div class="dfoot">
    <button class="settingsbtn" onclick={() => ($filesOpen = true)}>📁 Files</button>
    <button class="settingsbtn" onclick={() => ($settingsOpen = true)}>⚙ Settings</button>
  </div>
</div>
