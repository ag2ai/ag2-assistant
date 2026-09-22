<script lang="ts">
  import Icon from '../Icon.svelte'
  import A2UILink from './A2UILink.svelte'
  import BasicA2UIComponent from './BasicA2UIComponent.svelte'
  import WeatherBanner from './WeatherBanner.svelte'
  import { a2uiIconName, a2uiPresent, a2uiText, a2uiTone, a2uiValue, actionContext, axisScopes, bindingPath, childSlots, markedColumn, metricParts, rows, sparkPath, str, templateStart } from '../../lib/a2ui.ts'
  import type { A2UIAction, A2UIComponent, A2UIData, A2UIOption } from '../../lib/a2ui.ts'

  type Props = {
    component: A2UIComponent
    components?: A2UIComponent[]
    data?: A2UIData
    onDataChange?: (path: string, value: unknown) => void
    onAction?: (action: A2UIAction) => void
    scope?: string
    depth?: number
  }
  let {
    component,
    components = [],
    data = {},
    onDataChange = () => {},
    onAction = () => {},
    scope = '',
    depth = 0,
  }: Props = $props()
  // The component graph is agent-produced and children are resolved by id from a
  // flat list, so a cyclic (A→B→A) or self-referential graph would recurse without
  // bound and blow the stack. Cap the render depth — real layouts are shallow.
  const MAX_DEPTH = 24
  const type = $derived((component.component || 'Text').toLowerCase())
  const byId = $derived(new Map(components.filter((c) => c && c.id).map((c) => [c.id, c])))

  function list<T>(value: unknown): T[] {
    return rows<T>(value)
  }

  function child(id: unknown): A2UIComponent | undefined {
    return typeof id === 'string' ? byId.get(id) : undefined
  }

  const checkboxValue = $derived(!!a2uiValue(component.value, data, scope))
  const checkboxPath = $derived(bindingPath(component.value, scope))

  function toggleCheckbox(event: Event & { currentTarget: HTMLInputElement }) {
    if (checkboxPath) onDataChange(checkboxPath, event.currentTarget.checked)
  }

  const valuePath = $derived(bindingPath(component.value, scope))
  const inputValue = $derived(a2uiValue(component.value, data, scope) ?? '')
  // A bound value reaches an <input> as its text; only choicepicker reads the array.
  const inputText = $derived(Array.isArray(inputValue) ? '' : String(inputValue ?? ''))
  const sliderStep = $derived(
    component.steps ? (Number(component.max) - Number(component.min || 0)) / Number(component.steps) : undefined
  )
  // A missing bound stays absent so the attribute is omitted rather than NaN.
  const numOr = (v: unknown): number | undefined => (v == null || v === '' ? undefined : Number(v))
  const ICONS: Record<string, string | undefined> = { accountCircle: 'users', add: 'plus', arrowBack: 'chevron-left', arrowForward: 'chevron-right', attachFile: 'paperclip', calendarToday: 'clock', close: 'x', delete: 'trash', event: 'clock', favorite: 'thumbs-up', folder: 'folder', play: 'send', refresh: 'rotate-cw', send: 'send', settings: 'settings', stop: 'square', warning: 'alert-triangle' }
  // The glyph is what the Card's map names; the label is the word it stands for.
  const iconKey = $derived(a2uiIconName(component, data, scope))
  const iconName = $derived(ICONS[iconKey] || iconKey)
  const iconLabel = $derived(str(a2uiValue(component.name, data, scope)))
  // An icon's size is the same word a Metric and a Sparkline take, in pixels.
  const ICON_SIZE: Record<string, number> = { sm: 14, md: 22, lg: 28 }
  const videoUrl = $derived(String(a2uiValue(component.url, data, scope) ?? ''))
  const youtubeEmbed = $derived(youtubeUrl(videoUrl))

  function setValue(event: Event & { currentTarget: HTMLInputElement | HTMLTextAreaElement }) {
    if (valuePath) onDataChange(valuePath, event.currentTarget.value)
  }

  function setNumber(event: Event & { currentTarget: HTMLInputElement }) {
    if (valuePath) onDataChange(valuePath, Number(event.currentTarget.value))
  }

  function setDateTime(event: Event & { currentTarget: HTMLInputElement }) {
    if (!valuePath) return
    const value = event.currentTarget.value
    onDataChange(valuePath, component.enableDate && component.enableTime && value ? new Date(value).toISOString() : value)
  }

  function toggleChoice(option: unknown, selected: boolean) {
    if (!valuePath) return
    const current: unknown[] = Array.isArray(inputValue) ? inputValue : []
    const next = component.variant === 'multipleSelection'
      ? (selected ? [...new Set([...current, option])] : current.filter((value) => value !== option))
      : [option]
    onDataChange(valuePath, next)
  }

  function youtubeUrl(url: string) {
    try {
      const parsed = new URL(url)
      const hostname = parsed.hostname.replace(/^www\./, '').toLowerCase()
      const path = parsed.pathname.split('/').filter(Boolean)
      const id = hostname === 'youtu.be'
        ? path[0]
        : hostname === 'youtube.com' || hostname === 'youtube-nocookie.com'
          ? (path[0] === 'embed' || path[0] === 'shorts' ? path[1] : parsed.searchParams.get('v'))
          : ''
      return id && /^[\w-]{6,}$/.test(id) ? `https://www.youtube.com/embed/${id}` : ''
    } catch { return '' }
  }

  // A layout's cross-axis alignment, in the vocabulary the Basic Catalog declares.
  // Absent leaves the CSS default standing.
  const ALIGN: Record<string, string> = { start: 'flex-start', center: 'center', end: 'flex-end', stretch: 'stretch' }
  const align = $derived(ALIGN[String(component.align ?? '')] || undefined)

  // ── The styling vocabulary (ADR 0028) ──────────────────────────────────────
  // A Card names a gap, an alignment, a size or a tone; each resolves to a token.
  const GAP: Record<string, string> = { none: '0', xs: 'var(--space-2)', sm: 'var(--space-3)', md: 'var(--space-5)', lg: 'var(--space-7)' }
  const gap = $derived(GAP[String(component.gap ?? '')] || undefined)
  const JUSTIFY: Record<string, string> = { start: 'flex-start', center: 'center', end: 'flex-end', between: 'space-between' }
  const justify = $derived(JUSTIFY[String(component.justify ?? '')] || undefined)
  // A component that takes the room its row has left over.
  const grow = $derived(component.grow === true ? 1 : undefined)
  const tone = $derived(a2uiTone(component.tone, data, scope))
  // A component that says nothing about its tone keeps the class it already had.
  const toneClass = $derived(component.tone === undefined ? '' : `a2ui-tone-${tone}`)
  // Whether a component conditional on its data is drawn at all.
  const present = $derived(a2uiPresent(component.when, data, scope))
  // A rule down a layout's leading edge, in its own tone. A bound marker marks
  // only the rows its value is there for — the one event that is up next.
  const marked = $derived(component.marker !== undefined && a2uiPresent(component.marker, data, scope))
  const markerClass = $derived(marked ? `a2ui-marker a2ui-tone-${tone}` : '')

  // A ranked List numbers its rows from where the template starts in its array.
  const ranked = $derived(component.variant === 'ranked')
  const rankFrom = $derived(templateStart(component.children))

  const TEXT_VARIANTS = ['h1', 'h2', 'h3', 'h4', 'body', 'caption', 'eyebrow', 'quote', 'pill', 'badge']
  const textVariant = $derived(TEXT_VARIANTS.includes(String(component.variant ?? '')) ? String(component.variant) : '')
  const textValue = $derived(a2uiText(component, data, scope))

  const metric = $derived(metricParts(component, data, scope))
  const metricLabel = $derived(str(a2uiValue(component.label, data, scope)))
  const sizeName = $derived(['sm', 'md', 'lg'].includes(String(component.size ?? '')) ? String(component.size) : 'md')
  // The coordinate box a sparkline's 0..100 series is drawn in. The rendered size
  // is the matching .a2ui-spark-* rule's, which at lg is fluid.
  const SPARK: Record<string, { w: number; h: number; dot: number }> = { sm: { w: 78, h: 30, dot: 2.2 }, md: { w: 160, h: 56, dot: 2.6 }, lg: { w: 300, h: 120, dot: 3 } }
  const sparkBox = $derived(SPARK[sizeName])
  const spark = $derived(sparkPath(a2uiValue(component.values, data, scope), sparkBox.w, sparkBox.h))

  // ── The comparison table ───────────────────────────────────────────────────
  // Its two axes, and the column each mark falls in. A cell wins when the row's
  // `win` carries what the column's `key` carries; `pick` marks a whole column.
  const tableColumns = $derived(axisScopes(component.columns, data, scope))
  const tableRows = $derived(axisScopes(component.rows, data, scope))
  const tablePick = $derived(markedColumn(component.key, component.pick, data, tableColumns, scope))
  const tableLead = $derived(child(component.lead))
  const tableGrid = $derived(
    `${tableLead ? 'minmax(118px, .9fr) ' : ''}repeat(${tableColumns.length}, minmax(94px, 1fr))`
  )

  function clickButton() {
    const event = component.action?.event
    if (!event?.name) return
    onAction({ name: event.name, sourceComponentId: component.id, context: actionContext(event.context || {}, data, scope) })
  }
</script>

{#snippet kids()}
  {#each childSlots(component.children, data, scope) as slot}
    {@const kid = child(slot.id)}
    {#if kid}<BasicA2UIComponent component={kid} {components} {data} {onDataChange} {onAction} scope={slot.scope} depth={depth + 1} />{/if}
  {/each}
{/snippet}

{#if depth >= MAX_DEPTH}
  <!-- cyclic or pathologically deep component graph — stop recursing -->
{:else if !present}
  <!-- the data this component is conditional on is not there -->
{:else if type === 'column'}
  <div class="a2ui-basic-col {markerClass}" style:align-items={align} style:justify-content={justify} style:gap={gap} style:flex-grow={grow}>
    {@render kids()}
  </div>
{:else if type === 'row'}
  <div class="a2ui-basic-row {markerClass}" style:align-items={align} style:justify-content={justify} style:gap={gap} style:flex-grow={grow}>
    {@render kids()}
  </div>
{:else if type === 'list'}
  <div class="a2ui-list {markerClass}" class:a2ui-ranked={ranked} style:align-items={align} style:justify-content={justify} style:gap={gap} style:flex-grow={grow} style:--a2ui-rank-from={rankFrom}>
    {@render kids()}
  </div>
{:else if type === 'card'}
  {@const kid = child(component.child)}
  <div class="a2ui-basic-card {markerClass}" class:a2ui-feature={component.variant === 'feature'} style:flex-grow={grow}>
    {#if kid}<BasicA2UIComponent component={kid} {components} {data} {onDataChange} {onAction} {scope} depth={depth + 1} />{/if}
  </div>
{:else if type === 'link'}
  {@const kid = child(component.child)}
  <A2UILink {component} {data} {scope} {grow}>
    {#if kid}<BasicA2UIComponent component={kid} {components} {data} {onDataChange} {onAction} {scope} depth={depth + 1} />{/if}
  </A2UILink>
{:else if type === 'text'}
  <!-- Text that resolves to nothing draws nothing, so an optional field a Card
       binds leaves no blank line behind. -->
  {#if textValue}
    <div class="a2ui-text a2ui-tone-{tone} {textVariant ? `a2ui-t-${textVariant}` : ''}" class:a2ui-main={!textVariant && component.variant && component.variant !== 'body'} class:a2ui-strong={component.emphasis === 'strong'} style:flex-grow={grow}>{textValue}</div>
  {/if}
{:else if type === 'metric'}
  {#if metric.value || metric.delta}
    <div class="a2ui-metric a2ui-metric-{sizeName}" class:end={component.align === 'end'} style:flex-grow={grow}>
      {#if metricLabel}<div class="a2ui-metric-label">{metricLabel}</div>{/if}
      <div class="a2ui-metric-value">{metric.value}{#if metric.unit}<i>{metric.unit}</i>{/if}</div>
      {#if metric.delta}
        <div class="a2ui-metric-delta a2ui-tone-{tone}">{#if metric.arrow}<span class="a2ui-metric-arrow">{metric.arrow}</span>{/if}{metric.delta}</div>
      {/if}
    </div>
  {/if}
{:else if type === 'sparkline'}
  <!-- An empty series keeps its fixed column so neighbouring rows still line up;
       at lg, which has no column, it draws nothing. -->
  {#if !spark && sizeName !== 'lg'}
    <span class="a2ui-spark a2ui-spark-{sizeName}"></span>
  {:else if spark}
    <!-- One draw-in on mount, then still. -->
    <svg class="a2ui-spark a2ui-spark-{sizeName} a2ui-tone-{tone}" viewBox="0 0 {sparkBox.w} {sparkBox.h}" preserveAspectRatio="none" aria-hidden="true" style:flex-grow={grow}>
      {#if sizeName === 'lg'}<path d={spark.area} class="area" />{/if}
      <path d={spark.line} class="ln" pathLength="1" />
      <circle cx={spark.endX} cy={spark.endY} r={sparkBox.dot} class="end" />
    </svg>
  {/if}
{:else if type === 'table'}
  <!-- Nothing to compare against is no table at all; the grid scrolls inside its own
       frame rather than widening the Card it is in. -->
  {@const head = child(component.header)}
  {@const body = child(component.cell)}
  {#if tableColumns.length}
  <div class="a2ui-tablewrap" style:flex-grow={grow}>
    <div class="a2ui-table" style:grid-template-columns={tableGrid}>
      {#if head}
        {#if tableLead}<div class="a2ui-th"></div>{/if}
        {#each tableColumns as column, index}
          <div class="a2ui-th" class:pick={index === tablePick}>
            <BasicA2UIComponent component={head} {components} {data} {onDataChange} {onAction} scope={column} depth={depth + 1} />
          </div>
        {/each}
      {/if}
      {#each tableRows as row}
        {@const cells = axisScopes(component.cells, data, row)}
        {@const won = markedColumn(component.key, component.win, data, tableColumns, row)}
        {#if tableLead}
          <div class="a2ui-td a2ui-th-row">
            <BasicA2UIComponent component={tableLead} {components} {data} {onDataChange} {onAction} scope={row} depth={depth + 1} />
          </div>
        {/if}
        {#each tableColumns as _, index}
          <!-- A row with fewer cells than there are columns keeps the columns it
               does not fill, so every row still lines up under its option. -->
          <div class="a2ui-td" class:pick={index === tablePick} class:win={index === won}>
            {#if body && cells[index] !== undefined}
              <BasicA2UIComponent component={body} {components} {data} {onDataChange} {onAction} scope={cells[index]} depth={depth + 1} />
            {:else}
              <span class="a2ui-td-none">—</span>
            {/if}
            {#if index === won}<span class="a2ui-td-win" role="img" aria-label="Wins this row">●</span>{/if}
          </div>
        {/each}
      {/each}
    </div>
  </div>
  {/if}
{:else if type === 'divider'}
  <div class="a2ui-divider" class:strong={component.emphasis === 'strong'} aria-hidden="true"></div>
{:else if type === 'checkbox'}
  <label class="a2ui-checkbox">
    <input type="checkbox" checked={checkboxValue} onchange={toggleCheckbox} />
    <span>{a2uiValue(component.label, data, scope) || ''}</span>
  </label>
{:else if type === 'button'}
  <button class:primary={component.variant === 'primary'} class="a2ui-button" onclick={clickButton}>
    {a2uiValue(child(component.child)?.text, data, scope) || 'Continue'}
  </button>
{:else if type === 'image'}
  <!-- Default fit is `contain`, not `fill`: .a2ui-image clamps the box (width:100% +
       max-height), so the box rarely matches the image's intrinsic ratio — `fill` then
       stretches it. `contain` letterboxes against the tile's background instead, which
       is what that background colour is there for. Matches A2UI/BoxFit's own default. -->
  <img class="a2ui-image {component.variant || ''}" src={String(a2uiValue(component.url, data, scope) ?? '')} alt={String(a2uiValue(component.description, data, scope) ?? '')} style:object-fit={component.fit === 'scaleDown' ? 'scale-down' : component.fit || 'contain'} />
{:else if type === 'figure'}
  <!-- A lead media block: the picture cropped to fill its own box, with the
       credit stamped in the corner. An empty url is no figure at all. -->
  {@const url = String(a2uiValue(component.url, data, scope) ?? '')}
  {@const caption = str(a2uiValue(component.caption, data, scope))}
  {#if url}
    <figure class="a2ui-figure a2ui-figure-{sizeName}" style:flex-grow={grow}>
      <img src={url} alt={String(a2uiValue(component.description, data, scope) ?? '')} loading="lazy" />
      {#if caption}<figcaption>{caption}</figcaption>{/if}
    </figure>
  {/if}
{:else if type === 'weatherglyph'}
  <!-- The weather drawn as a band: the condition names the scene, the app-wide
       `animations` tier picks how richly it is drawn. -->
  <div class="a2ui-glyph a2ui-glyph-{sizeName}" style:flex-grow={grow}>
    {#key a2uiValue(component.condition, data, scope)}
      <WeatherBanner
        condition={a2uiValue(component.condition, data, scope)}
        temperatureText={str(a2uiValue(component.temperature, data, scope))}
        zoom={1.3}
        flush
      />
    {/key}
  </div>
{:else if type === 'icon'}
  <span class="a2ui-icon {toneClass}" title={iconLabel}><Icon name={iconName} size={ICON_SIZE[sizeName]} /></span>
{:else if type === 'video'}
  {#if youtubeEmbed}
    <iframe class="a2ui-video" src={youtubeEmbed} title="Video" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>
  {:else}
    <!-- A2UI carries no caption track, so the element declares an empty one
         rather than claiming captions it does not have. -->
    <video class="a2ui-video" controls src={videoUrl} poster={String(a2uiValue(component.posterUrl, data, scope) ?? '') || undefined}>
      <track kind="captions" />
    </video>
  {/if}
{:else if type === 'textfield'}
  <label class="a2ui-field">
    <span>{a2uiValue(component.label, data, scope) || ''}</span>
    {#if component.variant === 'longText'}
      <textarea value={inputText} placeholder={String(a2uiValue(component.placeholder, data, scope) ?? '')} oninput={setValue}></textarea>
    {:else}
      <input type={component.variant === 'number' ? 'number' : component.variant === 'obscured' ? 'password' : 'text'} value={inputText} placeholder={String(a2uiValue(component.placeholder, data, scope) ?? '')} oninput={setValue} />
    {/if}
  </label>
{:else if type === 'choicepicker'}
  <fieldset class="a2ui-choice">
    {#if component.label}<legend>{a2uiValue(component.label, data, scope)}</legend>{/if}
    <div class:chips={component.displayStyle === 'chips'}>
      {#each list<A2UIOption>(component.options) as option}
        {@const selected = Array.isArray(inputValue) && inputValue.includes(option.value)}
        <label>
          <input type={component.variant === 'multipleSelection' ? 'checkbox' : 'radio'} name={(component.id || '') + scope} checked={selected} onchange={(event) => toggleChoice(option.value, event.currentTarget.checked)} />
          <span>{a2uiValue(option.label, data, scope) || option.value}</span>
        </label>
      {/each}
    </div>
  </fieldset>
{:else if type === 'slider'}
  <label class="a2ui-field a2ui-slider">
    {#if component.label}<span>{a2uiValue(component.label, data, scope)}</span>{/if}
    <input type="range" min={numOr(component.min) ?? 0} max={numOr(component.max)} step={sliderStep} value={inputText} oninput={setNumber} />
    <output>{inputText}</output>
  </label>
{:else if type === 'datetimeinput'}
  <label class="a2ui-field">
    {#if component.label}<span>{a2uiValue(component.label, data, scope)}</span>{/if}
    <input type={component.enableDate && component.enableTime ? 'datetime-local' : component.enableDate ? 'date' : 'time'} value={component.enableDate && component.enableTime && inputText ? inputText.slice(0, 16) : inputText} min={String(a2uiValue(component.min, data, scope) ?? '') || undefined} max={String(a2uiValue(component.max, data, scope) ?? '') || undefined} onchange={setDateTime} />
  </label>
{:else}
  <div class="a2ui-basic-card">
    <div class="a2ui-main">{component.title || component.topic || component.component || 'Interactive view'}</div>
  </div>
{/if}
