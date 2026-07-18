<script>
  // Compact header over a run's chat thread: which task, when, current status,
  // with a Stop for live runs and a link back to the task page.
  import { runInfo } from '../../store.js'
  import { api } from '../../transport/api.js'
  import { go } from '../../router.js'
  import Icon from '../Icon.svelte'
  import { fmtStamp } from '../../lib/time.js'

  const TERMINAL = ['completed', 'failed', 'cancelled']
  const WORD = { completed: '✓ completed', failed: '✗ failed', cancelled: 'cancelled',
                 running: 'running…', needs_input: 'needs your input' }
</script>

{#if $runInfo}
  <div class="runbanner">
    <button class="runlink" onclick={() => go('/t/' + $runInfo.task_id)}>
      <Icon name="list" size={13} /> {$runInfo.task_name || 'Task'}
    </button>
    <span class="runmeta">{fmtStamp($runInfo.started_at)} · {WORD[$runInfo.status] || $runInfo.status}</span>
    {#if !TERMINAL.includes($runInfo.status)}
      <button class="open danger" onclick={() => api.stopRun($runInfo.id)}><Icon name="x" size={13} /> Stop run</button>
    {/if}
    {#if $runInfo.status === 'failed' && $runInfo.error}
      <span class="taskerror">{$runInfo.error}</span>
    {/if}
  </div>
{/if}

<style>
  .runbanner { display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
               padding: 8px 16px; border-bottom: 1px solid var(--line); font-size: var(--text-sm); }
  .runmeta { color: var(--muted); }

  /* Same rationale as TaskPage.svelte: these were app.css rules scoped to an
     ancestor (.panel/.modal) TaskPanel.svelte supplied — RunBanner's root has
     neither class, so reproduce them here, Svelte-scoped to this component. */
  .runlink { background: none; border: none; padding: 0; color: var(--accent, #d8552f); cursor: pointer; font: inherit; text-decoration: underline; text-underline-offset: 2px; display: inline-flex; align-items: center; gap: 5px; }
  .open {
    display: inline-flex; align-items: center; justify-content: center; gap: 6px;
    flex: none; font-family: var(--sans); font-size: 13px; font-weight: var(--fw-medium); cursor: pointer;
    border: 1px solid var(--line); background: var(--surface); color: var(--ink);
    border-radius: var(--radius-sm); padding: 7px 14px;
    transition: border-color var(--dur-fast) var(--ease-out), color var(--dur-fast) var(--ease-out), background var(--dur-fast) var(--ease-out);
  }
  .open.danger { border-color: color-mix(in srgb, #d8552f 55%, var(--line)); color: #d8552f; background: none; }
  .open.danger:hover { border-color: #d8552f; color: #fff; background: #d8552f; }
  .taskerror { display: flex; align-items: flex-start; gap: 7px; font-size: var(--text-sm); line-height: var(--leading-snug); color: var(--ag2-observer); background: color-mix(in srgb, var(--ag2-observer) 9%, transparent); border: 1px solid color-mix(in srgb, var(--ag2-observer) 28%, transparent); border-radius: var(--radius-sm); padding: 6px 10px; overflow-wrap: anywhere; }
</style>
