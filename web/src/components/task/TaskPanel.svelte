<script>
  import { taskPanel } from '../../store.js'
  import { api } from '../../transport/api.js'
  import { go, newChatId } from '../../router.js'
  import Icon from '../Icon.svelte'
  import { fmtStamp, fmtDateTime } from '../../lib/time.js'

  const TERMINAL = ['completed', 'failed', 'cancelled']
  let rerunning = $state(false)
  let confirmDel = $state(false)
  let deleting = $state(false)

  // When this run executed — uses the task's server timestamps (ISO), which
  // survive replay. A run is one execution window, so this answers "when was all
  // this produced" at the top of the page.
  const runTime = $derived.by(() => {
    const p = $taskPanel
    if (!p) return ''
    if (p.ended_at) {
      const word = { completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled' }[p.status] || 'Ended'
      return `${word} ${fmtStamp(p.ended_at)}`
    }
    if (p.started_at) return `Started ${fmtStamp(p.started_at)}`
    if (p.created_at) return `Created ${fmtStamp(p.created_at)}`
    return ''
  })

  async function cancel() {
    if ($taskPanel) { await api.cancelTask($taskPanel.id); }
  }
  // Permanent delete: removes the task, its subtree, and their chat/event streams.
  // Cancels an in-flight run first (server-side). The task is gone, so leave the page.
  async function del() {
    if (!$taskPanel || deleting) return
    deleting = true
    try {
      await api.deleteTask($taskPanel.id)
      go('/c/' + newChatId())
    } catch {
      deleting = false
    }
  }
  // Re-run a finished task from a clean start — runs as a fresh occurrence and
  // navigates to it (the original failed/finished run stays as history).
  async function rerun() {
    if (!$taskPanel || rerunning) return
    rerunning = true
    try {
      const res = await api.rerunTask($taskPanel.id)
      if (res && res.id) go('/t/' + res.id)
    } finally {
      rerunning = false
    }
  }
</script>

{#if $taskPanel}
  <div class="panel">
    <div class="ptitle">Task</div>
    {#if $taskPanel.objective}<div>{$taskPanel.objective}</div>{/if}
    {#if runTime}<div class="tasktime"><Icon name="clock" size={13} /> {runTime}</div>{/if}
    {#if $taskPanel.scheduled_for}
      <div class="note" style="text-align:left;margin:6px 0;display:inline-flex;align-items:center;gap:6px" title={$taskPanel.recurrence ? 'repeats ' + $taskPanel.recurrence : ''}><Icon name="clock" size={13} /> {fmtDateTime($taskPanel.scheduled_for)}{$taskPanel.recurrence ? ' · ' + ($taskPanel.recurrence_desc || 'repeats ' + $taskPanel.recurrence) : ' (one-off)'}</div>
    {/if}
    {#if $taskPanel.run_of}
      <div class="note" style="text-align:left;display:inline-flex;align-items:center;gap:6px">
        <Icon name="zap" size={13} /> One run of a
        <button class="runlink" onclick={() => go('/t/' + $taskPanel.run_of)}>recurring task</button>
      </div>
    {/if}

    {#if $taskPanel.status === 'failed' && $taskPanel.error}
      <div class="taskerror"><Icon name="x" size={13} /> <span><strong>Failed:</strong> {$taskPanel.error}</span></div>
    {/if}

    <!-- Deliverable output is shown once, in the thread's "Deliverable produced"
         item below — the panel stays a compact header (no repeated body). -->

    {#each ($taskPanel.children || []) as c}
      <div class="child"><span class="badge">{c.status}</span> {c.title}</div>
    {/each}

    <!-- Occurrences spawned from this recurring template (newest first) — each run
         is its own task, so link to where its output actually lives. -->
    {#if ($taskPanel.runs || []).length}
      <div class="ptitle" style="margin-top:12px">Runs</div>
      {#each $taskPanel.runs as r (r.id)}
        <div class="child">
          <span class="badge">{r.status}</span>
          <button class="runlink" onclick={() => go('/t/' + r.id)}>{fmtStamp(r.created_at)}</button>
        </div>
      {/each}
    {/if}

    <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
      {#if !TERMINAL.includes($taskPanel.status)}
        <button class="open" onclick={cancel}><Icon name="x" size={14} /> Cancel task</button>
      {/if}
      {#if TERMINAL.includes($taskPanel.status)}
        <button class="open" disabled={rerunning} onclick={rerun}><Icon name="rotate-cw" size={14} /> {rerunning ? 'Starting…' : 'Run again'}</button>
      {/if}
      {#if confirmDel}
        <span class="delconfirm">
          <span class="confirm">Delete permanently?</span>
          <button class="open danger" disabled={deleting} onclick={del}>{deleting ? 'Deleting…' : 'Yes, delete'}</button>
          <button class="open" disabled={deleting} onclick={() => (confirmDel = false)}>Cancel</button>
        </span>
      {:else}
        <button class="open danger" onclick={() => (confirmDel = true)}><Icon name="trash" size={14} /> Delete</button>
      {/if}
    </div>
  </div>
{/if}
