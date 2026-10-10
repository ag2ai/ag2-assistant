<script lang="ts">
  import { a2uiValue } from '../../lib/a2ui.ts'
  import type { A2UIComponent, A2UIData } from '../../lib/a2ui.ts'
  import { calendarHeatmap, CALENDAR_STATUSES } from '../../lib/calendarHeatmap.ts'

  let { component, data, scope = '' }: { component: A2UIComponent; data: A2UIData; scope?: string } = $props()
  const model = $derived(calendarHeatmap(a2uiValue(component.startDate, data, scope), a2uiValue(component.endDate, data, scope), a2uiValue(component.days, data, scope), String(component.weekStartsOn || 'monday')))
  const locale = $derived(String(a2uiValue(component.locale, data, scope) || 'en'))
  const labels = $derived(locale.toLowerCase().startsWith('ru') ? {
    activity: 'Занятие', analysis: 'Анализ', pause: 'Пауза', missed: 'Пропуск', unknown: 'Нет данных', upcoming: 'Впереди',
  } : { activity: 'Activity', analysis: 'Analysis', pause: 'Pause', missed: 'Missed', unknown: 'Unknown', upcoming: 'Upcoming' })
  const size = $derived(component.cellSize === 'sm' ? 10 : component.cellSize === 'lg' ? 18 : 14)
  const weekStart = $derived(component.weekStartsOn === 'sunday' ? 0 : 1)
  function labelDate(date: string, options: Intl.DateTimeFormatOptions): string {
    try { return new Intl.DateTimeFormat(locale, { ...options, timeZone: 'UTC' }).format(new Date(date + 'T00:00:00Z')) }
    catch { return new Intl.DateTimeFormat('en', { ...options, timeZone: 'UTC' }).format(new Date(date + 'T00:00:00Z')) }
  }
</script>

{#if model.error}
  <p class="calendar-error" role="alert">{model.error}</p>
{:else}
  <div class="calendar-root" style:--calendar-cell={`${size}px`} style:--calendar-weeks={model.weeks} role="group" aria-label={`${'Activity calendar'}: ${a2uiValue(component.startDate, data, scope)} – ${a2uiValue(component.endDate, data, scope)}`}>
    <div class="calendar-scroll">
      <div class="calendar-grid">
        {#each model.months as month, index}
          <span class="calendar-month" style:grid-column={`${month.column + 2} / span ${Math.max(1, Math.min(3, (model.months[index + 1]?.column ?? model.weeks) - month.column))}`} style:grid-row={1} aria-hidden="true">{labelDate(month.date, { month: 'short' })}</span>
        {/each}
        {#each Array(7) as _, row}
          <span class="calendar-weekday" style:grid-column={1} style:grid-row={row + 2} aria-hidden="true">{labelDate(`2024-01-0${(row + weekStart + 6) % 7 + 1}`, { weekday: 'short' })}</span>
        {/each}
        {#each model.days as day (day.date)}
          {#if day.status === 'outside'}
            <span class="calendar-day outside" style:grid-column={day.column + 2} style:grid-row={day.row + 2} aria-hidden="true"></span>
          {:else}
            {@const label = day.label || `${labelDate(day.date, { dateStyle: 'medium' })}: ${labels[day.status]}${day.count === undefined ? '' : ` (${day.count})`}`}
            <span class="calendar-day {day.status} level-{day.level}" data-date={day.date} data-status={day.status} style:grid-column={day.column + 2} style:grid-row={day.row + 2} role="img" aria-label={label} title={label}></span>
          {/if}
        {/each}
      </div>
    </div>
    {#if component.showLegend !== false}
      <div class="calendar-legend" aria-label={locale.startsWith('ru') ? 'Легенда' : 'Legend'}>
        {#each CALENDAR_STATUSES as status}
          <span><i class="calendar-day {status} level-4" aria-hidden="true"></i>{labels[status]}</span>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .calendar-root { width: fit-content; max-width: 100%; min-width: 0; }
  .calendar-scroll { max-width: 100%; overflow-x: auto; padding-bottom: var(--space-2); }
  .calendar-grid { display: grid; width: max-content; grid-template-columns: 30px repeat(var(--calendar-weeks), var(--calendar-cell)); grid-template-rows: 18px repeat(7, var(--calendar-cell)); gap: 2px; }
  .calendar-month { font-size: 10px; color: var(--muted); white-space: nowrap; overflow: hidden; align-self: start; }
  .calendar-weekday { font-size: 9px; color: var(--muted); align-self: center; }
  .calendar-day { display: inline-block; width: var(--calendar-cell); height: var(--calendar-cell); border: 1px solid var(--line); border-radius: 2px; background: var(--surface); }
  .calendar-day.outside { visibility: hidden; }
  .calendar-day.activity { border-color: color-mix(in srgb, var(--success) 70%, var(--line)); background: color-mix(in srgb, var(--success) var(--level), var(--surface)); }
  .calendar-day.level-1 { --level: 25%; } .calendar-day.level-2 { --level: 45%; } .calendar-day.level-3 { --level: 70%; } .calendar-day.level-4 { --level: 100%; }
  .calendar-day.analysis { border-color: var(--accent); background: repeating-linear-gradient(135deg, var(--accent-soft) 0 3px, var(--accent) 3px 5px); }
  .calendar-day.pause { background: linear-gradient(90deg, transparent 28%, var(--muted) 28% 42%, transparent 42% 58%, var(--muted) 58% 72%, transparent 72%); }
  .calendar-day.missed { border-color: var(--danger); background: color-mix(in srgb, var(--danger) 35%, var(--surface)); }
  .calendar-day.unknown { border-style: dashed; background: transparent; }
  .calendar-day.upcoming { opacity: .35; background: transparent; }
  .calendar-legend { display: grid; grid-template-columns: repeat(2, max-content); gap: var(--space-2) var(--space-3); font-size: 10px; color: var(--muted); }
  .calendar-legend span { display: inline-flex; align-items: center; gap: var(--space-2); }
  .calendar-error { color: var(--danger); font-size: 12px; overflow-wrap: anywhere; }
</style>
