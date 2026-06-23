<script>
  import { taskPanel } from '../../store.js'
  import { api } from '../../transport/api.js'
  import { go } from '../../router.js'
  import Icon from '../Icon.svelte'
  import { fmtStamp, fmtDateTime } from '../../lib/time.js'

  const TERMINAL = ['completed', 'failed', 'cancelled']
  let rerunning = $state(false)

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
  async function archive() {
    if ($taskPanel) { await api.archiveTask($taskPanel.id, !$taskPanel.archived) }
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
      <div class="note" style="text-align:left;margin:6px 0;display:inline-flex;align-items:center;gap:6px"><Icon name="clock" size={13} /> {fmtDateTime($taskPanel.scheduled_for)}{$taskPanel.recurrence ? ' · repeats ' + $taskPanel.recurrence : ' (one-off)'}</div>
    {/if}
    {#if $taskPanel.run_of}<div class="note" style="text-align:left;display:inline-flex;align-items:center;gap:6px"><Icon name="zap" size={13} /> One run of a recurring task.</div>{/if}

    {#if $taskPanel.status === 'failed' && $taskPanel.error}
      <div class="taskerror"><Icon name="x" size={13} /> <span><strong>Failed:</strong> {$taskPanel.error}</span></div>
    {/if}

    <!-- Deliverable output is shown once, in the thread's "Deliverable produced"
         item below — the panel stays a compact header (no repeated body). -->

    {#each ($taskPanel.children || []) as c}
      <div class="child"><span class="badge">{c.status}</span> {c.title}</div>
    {/each}

    <div style="margin-top:10px;display:flex;gap:8px">
      {#if !TERMINAL.includes($taskPanel.status)}
        <button class="open" onclick={cancel}><Icon name="x" size={14} /> Cancel task</button>
      {/if}
      {#if TERMINAL.includes($taskPanel.status)}
        <button class="open" disabled={rerunning} onclick={rerun}><Icon name="rotate-cw" size={14} /> {rerunning ? 'Starting…' : 'Run again'}</button>
      {/if}
      {#if $taskPanel.archived || TERMINAL.includes($taskPanel.status)}
        <button class="open" onclick={archive}><Icon name="folder" size={14} /> {$taskPanel.archived ? 'Unarchive' : 'Archive'}</button>
      {/if}
    </div>
  </div>
{/if}
