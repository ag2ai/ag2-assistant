<script>
  // Maps UI presets ⇄ the schedule union {kind, at, cron}. Presets serialise to
  // cron; anything unrecognised round-trips through the Custom field untouched.
  let { schedule = $bindable() } = $props()

  const PRESETS = [
    { id: 'manual',   label: 'Manual' },
    { id: 'hourly',   label: 'Hourly' },
    { id: 'daily',    label: 'Daily' },
    { id: 'weekly',   label: 'Weekly' },
    { id: 'weekdays', label: 'Weekdays' },
    { id: 'custom',   label: 'Custom cron' },
    { id: 'once',     label: 'Once at…' },
  ]

  function detect(s) {
    if (s.kind === 'manual') return 'manual'
    if (s.kind === 'once') return 'once'
    const c = s.cron || ''
    if (c === '0 * * * *') return 'hourly'
    let m
    if ((m = c.match(/^(\d+) (\d+) \* \* \*$/))) return 'daily'
    if ((m = c.match(/^(\d+) (\d+) \* \* 1$/))) return 'weekly'
    if ((m = c.match(/^(\d+) (\d+) \* \* 1-5$/))) return 'weekdays'
    return 'custom'
  }
  function timeOf(cron) {
    const m = (cron || '').match(/^(\d+) (\d+) /)
    return m ? String(m[2]).padStart(2, '0') + ':' + String(m[1]).padStart(2, '0') : '09:00'
  }

  let preset = $state(detect(schedule))
  let time = $state(timeOf(schedule.cron))            // HH:MM for daily/weekly/weekdays
  let cron = $state(schedule.cron || '')              // raw, for custom
  let at = $state(schedule.at ? schedule.at.slice(0, 16) : '')  // datetime-local

  function apply() {
    const [h, m] = (time || '09:00').split(':').map(Number)
    if (preset === 'manual') schedule = { kind: 'manual', at: null, cron: null }
    else if (preset === 'once') schedule = { kind: 'once', at: at ? new Date(at).toISOString() : null, cron: null }
    else if (preset === 'hourly') schedule = { kind: 'cron', at: null, cron: '0 * * * *' }
    else if (preset === 'daily') schedule = { kind: 'cron', at: null, cron: `${m} ${h} * * *` }
    else if (preset === 'weekly') schedule = { kind: 'cron', at: null, cron: `${m} ${h} * * 1` }
    else if (preset === 'weekdays') schedule = { kind: 'cron', at: null, cron: `${m} ${h} * * 1-5` }
    else schedule = { kind: 'cron', at: null, cron: cron.trim() }
  }
</script>

<div class="schedfield">
  <select bind:value={preset} onchange={apply}>
    {#each PRESETS as p}<option value={p.id}>{p.label}</option>{/each}
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
  .schedfield { display: flex; gap: 8px; align-items: center; }
  .schedfield select, .schedfield input { font: inherit; padding: 6px 8px; }
</style>
