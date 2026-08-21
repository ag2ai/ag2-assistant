<script lang="ts">
  // Maps UI presets ⇄ the schedule union {kind, at, cron}. Presets serialise to
  // cron; anything unrecognised round-trips through the Custom field untouched.

  import type { ScheduleValue } from '../../lib/taskEdit.ts'
  import { m } from '../../paraglide/messages.js'

  type Preset = 'manual' | 'once' | 'hourly' | 'daily' | 'weekly' | 'weekdays' | 'custom'
  type Props = { schedule: ScheduleValue }

  let { schedule = $bindable() }: Props = $props()

  // The preset id is what serialises to cron; only its label localizes.
  const PRESETS: { id: Preset; label: () => string }[] = [
    { id: 'manual',   label: m.task_sched_manual },
    { id: 'hourly',   label: m.task_sched_hourly },
    { id: 'daily',    label: m.task_sched_daily },
    { id: 'weekly',   label: m.task_sched_weekly },
    { id: 'weekdays', label: m.task_sched_weekdays },
    { id: 'custom',   label: m.task_sched_custom },
    { id: 'once',     label: m.task_sched_once },
  ]

  function detect(s: ScheduleValue): Preset {
    if (s.kind === 'manual') return 'manual'
    if (s.kind === 'once') return 'once'
    const c = s.cron || ''
    if (c === '0 * * * *') return 'hourly'
    if (/^\d+ \d+ \* \* \*$/.test(c)) return 'daily'
    if (/^\d+ \d+ \* \* 1$/.test(c)) return 'weekly'
    if (/^\d+ \d+ \* \* 1-5$/.test(c)) return 'weekdays'
    return 'custom'
  }
  function timeOf(cron: string | null): string {
    const hm = (cron || '').match(/^(\d+) (\d+) /)
    return hm ? String(hm[2]).padStart(2, '0') + ':' + String(hm[1]).padStart(2, '0') : '09:00'
  }

  let preset = $state(detect(schedule))
  let time = $state(timeOf(schedule.cron))            // HH:MM for daily/weekly/weekdays
  let cron = $state(schedule.cron || '')              // raw, for custom
  let at = $state(schedule.at ? schedule.at.slice(0, 16) : '')  // datetime-local

  function apply() {
    const [h, min] = (time || '09:00').split(':').map(Number)
    if (preset === 'manual') schedule = { kind: 'manual', at: null, cron: null }
    else if (preset === 'once') schedule = { kind: 'once', at: at ? new Date(at).toISOString() : null, cron: null }
    else if (preset === 'hourly') schedule = { kind: 'cron', at: null, cron: '0 * * * *' }
    else if (preset === 'daily') schedule = { kind: 'cron', at: null, cron: `${min} ${h} * * *` }
    else if (preset === 'weekly') schedule = { kind: 'cron', at: null, cron: `${min} ${h} * * 1` }
    else if (preset === 'weekdays') schedule = { kind: 'cron', at: null, cron: `${min} ${h} * * 1-5` }
    else schedule = { kind: 'cron', at: null, cron: cron.trim() }
  }
</script>

<div class="schedfield">
  <select class="chpick" bind:value={preset} onchange={apply}>
    {#each PRESETS as p}<option value={p.id}>{p.label()}</option>{/each}
  </select>
  {#if ['daily', 'weekly', 'weekdays'].includes(preset)}
    <input type="time" bind:value={time} onchange={apply} />
  {:else if preset === 'custom'}
    <input type="text" placeholder="0 9 * * 1-5" bind:value={cron} onchange={apply} />
  {:else if preset === 'once'}
    <input type="datetime-local" bind:value={at} onchange={apply} />
  {/if}
</div>

<style>
  /* Same field-control recipe as TaskPage's .tpfield (which this select+input row
     sits inside, as the Schedule field) — a bordered control on the base surface,
     matching Settings' .llmfield inputs. Kept local since this component has no
     .settings/.tpfield ancestor class to inherit from. */
  .schedfield { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .schedfield select, .schedfield input {
    font: inherit; font-size: 13px; color: var(--ink);
    min-width: 0; border: 1px solid var(--line); border-radius: 8px; padding: 7px 9px;
    background-color: var(--bg);
  }
  .schedfield select { flex: none; padding-right: 30px; } /* clears the shared chevron */
  .schedfield input { flex: 1; min-width: 140px; }
  .schedfield select:focus, .schedfield input:focus {
    outline: none; border-color: var(--accent); box-shadow: var(--focus-ring);
  }
</style>
