<script lang="ts">
  import { a2uiValue } from '../../lib/a2ui.ts'
  import type { A2UIComponent, A2UIData } from '../../lib/a2ui.ts'
  import { chartGeometry, readChart } from '../../lib/chart.ts'
  import type { ChartVariant } from '../../lib/chart.ts'
  import type { ChartSeries } from '../../schemas/chart.ts'

  let { component, data = {}, scope = '' }: { component: A2UIComponent; data?: A2UIData; scope?: string } = $props()
  const uid = $props.id()
  const value = (key: string) => a2uiValue(component[key], data, scope)
  const parsed = $derived(readChart(value('labels'), value('series'), component.annotations === undefined ? [] : value('annotations'), component.variant, component.stacked))
  const locale = $derived(String(value('locale') || 'en'))
  const ru = $derived(locale.toLowerCase().startsWith('ru'))
  const formatter = $derived.by(() => {
    try { return new Intl.NumberFormat(locale, { maximumSignificantDigits: 12 }) }
    catch { return new Intl.NumberFormat('en', { maximumSignificantDigits: 12 }) }
  })
  const xLabel = $derived(String(value('xLabel') || ''))
  const yLabel = $derived(String(value('yLabel') || ''))
  const description = $derived(String(value('description') || (ru ? 'График' : 'Chart')))
  let containerWidth = $state(640)
  let hidden = $state(new Set<number>())
  let showData = $state(false)
  const HEIGHT: Record<string, number> = { sm: 220, md: 280, lg: 360 }
  const geometry = $derived(parsed.data ? chartGeometry(parsed.data, component.variant as ChartVariant, component.stacked === true, containerWidth, HEIGHT[String(component.size)] || HEIGHT.md, hidden) : null)
  const TONES: Record<string, string> = { neutral: 'var(--text-muted)', muted: 'var(--text-faint)', accent: 'var(--accent)', positive: 'var(--success)', negative: 'var(--danger)' }
  const color = (series: ChartSeries, index: number) => TONES[series.tone || ''] || `var(--chart-series-${index + 1})`
  const format = (number: number | null) => number === null ? (ru ? 'нет данных' : 'no data') : formatter.format(number)

  function tick(number: number) {
    if (number === 0) return '0'
    try {
      return new Intl.NumberFormat(formatter.resolvedOptions().locale, {
        maximumSignificantDigits: 4,
        notation: Math.abs(number) >= 10_000 ? 'compact' : Math.abs(number) < 0.0001 ? 'scientific' : 'standard',
      }).format(number)
    } catch { return format(number) }
  }

  function toggle(index: number) {
    const next = new Set(hidden)
    if (next.has(index)) next.delete(index)
    else next.add(index)
    hidden = next
  }

  function categoryTitle(index: number) {
    if (!parsed.data) return ''
    return [parsed.data.labels[index], ...parsed.data.series.map((series, i) => hidden.has(i) ? '' : `${series.label}: ${format(series.values[index])}`)].filter(Boolean).join('\n')
  }
</script>

<div class="chart" bind:clientWidth={containerWidth}>
  {#if parsed.data && geometry}
    {#if yLabel}<div class="axis-label">{yLabel}</div>{/if}
    <div class="plot-scroll" data-a2ui-scroll-region={component.id}>
      <svg width={geometry.width} height={geometry.height} viewBox="0 0 {geometry.width} {geometry.height}" role="img" aria-labelledby="{uid}-title" aria-describedby="{uid}-description">
        <title id="{uid}-title">{description}</title>
        <desc id="{uid}-description">{[xLabel, yLabel, ...parsed.data.series.map(series => series.label)].filter(Boolean).join(' · ')}. {ru ? 'Точные значения доступны в таблице под графиком.' : 'Exact values are available in the data table below.'}</desc>
        {#each geometry.ticks as mark}
          <line class="grid-line" x1={geometry.left} x2={geometry.right} y1={mark.y} y2={mark.y} />
          <text class="tick" x={geometry.left - 10} y={mark.y + 4} text-anchor="end">{tick(mark.value)}</text>
        {/each}
        <line class="baseline" x1={geometry.left} x2={geometry.right} y1={geometry.baseline} y2={geometry.baseline} />
        {#each geometry.categories as category}
          {@const pitch = (geometry.right - geometry.left) / geometry.categories.length}
          <rect class="category-hit" x={category.x - pitch / 2} y={geometry.top} width={pitch} height={geometry.bottom - geometry.top}>
            <title>{categoryTitle(category.index)}</title>
          </rect>
          {#if category.index % Math.max(1, Math.ceil(72 / pitch)) === 0}
            <text class="tick" x={category.x} y={geometry.height - 22} text-anchor="middle"><title>{category.label}</title>{category.label.length > 10 ? category.label.slice(0, 9) + '…' : category.label}</text>
          {/if}
        {/each}
        {#each geometry.bars as bar}
          <rect class="bar" data-series={bar.series} data-category={bar.category} x={bar.x} y={bar.y} width={bar.width} height={bar.height} fill={color(parsed.data.series[bar.series], bar.series)}>
            <title>{parsed.data.labels[bar.category]} · {parsed.data.series[bar.series].label}: {format(bar.value)}</title>
          </rect>
        {/each}
        {#each geometry.lines as line}
          {@const series = parsed.data.series[line.series]}
          <g style:color={color(series, line.series)}>
            {#each line.paths as path}<path class="series-line" d={path} />{/each}
            {#each line.points as point}
              <circle class="point" data-series={line.series} data-category={point.category} cx={point.x} cy={point.y} r="3.5"><title>{parsed.data.labels[point.category]} · {series.label}: {format(point.value)}</title></circle>
            {/each}
          </g>
        {/each}
        {#each geometry.annotations as annotation, index}
          <line class="annotation" x1={annotation.x} x2={annotation.x} y1={geometry.top} y2={geometry.bottom} />
          <text class="annotation-label" x={annotation.x} y={12 + (index % 2) * 14} text-anchor={annotation.x > (geometry.left + geometry.right) / 2 ? 'end' : 'start'}><title>{annotation.label}</title>{annotation.label.length > 30 ? annotation.label.slice(0, 29) + '…' : annotation.label}</text>
        {/each}
      </svg>
    </div>
    {#if xLabel}<div class="axis-label x-label">{xLabel}</div>{/if}
    {#if component.showLegend !== false}
      <div class="legend" aria-label={ru ? 'Ряды данных' : 'Data series'}>
        {#each parsed.data.series as series, index}
          <button class="legend-item" class:inactive={hidden.has(index)} aria-pressed={!hidden.has(index)} onclick={() => toggle(index)} title={ru ? 'Показать или скрыть ряд' : 'Show or hide series'}>
            <span class="swatch" style:background={color(series, index)}></span><span>{series.label}</span>
          </button>
        {/each}
      </div>
    {/if}
    <button class="data-toggle" aria-expanded={showData} aria-controls="{uid}-data" onclick={() => showData = !showData}>{showData ? (ru ? 'Скрыть данные' : 'Hide data') : (ru ? 'Показать данные' : 'Show data')}</button>
    {#if showData}
      <div class="data-scroll" id="{uid}-data">
        <table>
          <caption>{description}</caption>
          <thead><tr><th scope="col">{xLabel || (ru ? 'Период' : 'Category')}</th>{#each parsed.data.series as series}<th scope="col">{series.label}</th>{/each}</tr></thead>
          <tbody>{#each parsed.data.labels as label, index}<tr><th scope="row">{label}</th>{#each parsed.data.series as series}<td>{format(series.values[index])}</td>{/each}</tr>{/each}</tbody>
        </table>
      </div>
    {/if}
  {:else}
    <p class="chart-error" role="status">{ru ? 'Не удалось отобразить график' : 'Unable to display chart'}: {parsed.error}</p>
  {/if}
</div>

<style>
  .chart { min-width: 0; max-width: 100%; width: 100%; }
  .plot-scroll, .data-scroll { max-width: 100%; overflow-x: auto; overscroll-behavior-x: contain; }
  svg { display: block; font-family: inherit; }
  .axis-label { color: var(--text-muted); font-size: var(--text-sm); overflow-wrap: anywhere; }
  .x-label { text-align: center; }
  .tick { fill: var(--text-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
  .grid-line { stroke: var(--line); stroke-width: 1; }
  .baseline { stroke: var(--line-strong); stroke-width: 1; }
  .category-hit { fill: transparent; }
  .series-line { stroke: currentColor; stroke-width: 2; fill: none; }
  .point { fill: currentColor; stroke: var(--surface); stroke-width: 1.5; }
  .bar:hover, .point:hover { opacity: .8; }
  .annotation { stroke: var(--text-muted); stroke-width: 1; stroke-dasharray: 4 4; pointer-events: none; }
  .annotation-label { fill: var(--text-muted); font-size: 11px; }
  .legend { display: flex; flex-wrap: wrap; gap: var(--space-2); margin-top: var(--space-2); }
  .legend-item, .data-toggle { border: 1px solid var(--line); border-radius: var(--radius-sm); background: var(--surface); color: var(--text); cursor: pointer; font: inherit; font-size: var(--text-sm); padding: var(--space-2) var(--space-3); }
  .legend-item { display: inline-flex; align-items: center; gap: var(--space-2); max-width: 100%; text-align: start; overflow-wrap: anywhere; }
  .legend-item:hover, .data-toggle:hover { background: var(--surface-hover); border-color: var(--line-strong); }
  .legend-item:focus-visible, .data-toggle:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .inactive { opacity: .5; }
  .swatch { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
  .data-toggle { margin-top: var(--space-3); }
  table { border-collapse: collapse; font-size: var(--text-sm); font-variant-numeric: tabular-nums; margin-top: var(--space-3); }
  caption { text-align: start; color: var(--text-muted); margin-bottom: var(--space-2); }
  th, td { padding: var(--space-2) var(--space-3); border-bottom: 1px solid var(--line); text-align: end; white-space: nowrap; }
  th:first-child { text-align: start; }
  .chart-error { color: var(--text-muted); overflow-wrap: anywhere; }
</style>
