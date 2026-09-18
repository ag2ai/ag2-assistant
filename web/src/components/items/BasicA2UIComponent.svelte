<script lang="ts">
  import Icon from '../Icon.svelte'
  import BasicA2UIComponent from './BasicA2UIComponent.svelte'
  import { a2uiValue, actionContext, bindingPath, childSlots, rows } from '../../lib/a2ui.ts'
  import type { A2UIAction, A2UIComponent, A2UIData, A2UIOption, NewsStory, WeatherRow } from '../../lib/a2ui.ts'

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

  function storySummary(story: NewsStory): string {
    return story.summary || story.detail || story.text || ''
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
  const iconKey = $derived(String(a2uiValue(component.name, data, scope) ?? ''))
  const iconName = $derived(ICONS[iconKey] || iconKey)
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
{:else if type === 'column'}
  <div class="a2ui-basic-col">
    {@render kids()}
  </div>
{:else if type === 'row'}
  <div class="a2ui-basic-row">
    {@render kids()}
  </div>
{:else if type === 'list'}
  <div class="a2ui-list">
    {@render kids()}
  </div>
{:else if type === 'card'}
  {@const kid = child(component.child)}
  <div class="a2ui-basic-card">
    {#if kid}<BasicA2UIComponent component={kid} {components} {data} {onDataChange} {onAction} {scope} depth={depth + 1} />{/if}
  </div>
{:else if type === 'text'}
  <div class:a2ui-main={component.variant && component.variant !== 'body'} class="a2ui-text">{a2uiValue(component.text, data, scope) || ''}</div>
{:else if type === 'divider'}
  <div class="a2ui-divider" aria-hidden="true"></div>
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
{:else if type === 'icon'}
  <span class="a2ui-icon" title={String(a2uiValue(component.name, data, scope) || '')}><Icon name={iconName} size={22} /></span>
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
{:else if type === 'weatherpanel'}
  <div class="a2ui-basic-card">
    <div class="a2ui-weather-top">
      <div>
        <div class="a2ui-main">{component.location || 'Requested location'}</div>
        <div class="a2ui-sub">Forecast summary</div>
      </div>
      <span class="a2ui-weather-glyph"><Icon name="sun" size={22} /></span>
    </div>
    <div class="a2ui-grid">
      {#each list<WeatherRow>(component.rows) as row}
        <div class="a2ui-cell">
          <div class="a2ui-label">{row.label}</div>
          <div>{row.value}</div>
        </div>
      {/each}
    </div>
  </div>
{:else if type === 'newsdigest'}
  <div class="a2ui-basic-card">
    <div class="a2ui-main">{component.topic || 'Latest news'}</div>
    <div class="a2ui-list">
      {#each list<NewsStory>(component.stories) as story}
        <div class="a2ui-story">
          <span><Icon name="globe" size={13} /></span>
          <div>
            {#if storySummary(story)}
              <details class="a2ui-details">
                <summary><strong>{story.title}</strong></summary>
                <p>{storySummary(story)}</p>
              </details>
            {:else}
              <strong>{story.title}</strong>
            {/if}
            <small>{story.meta}</small>
          </div>
        </div>
      {/each}
    </div>
  </div>
{:else}
  <div class="a2ui-basic-card">
    <div class="a2ui-main">{component.title || component.topic || component.component || 'Interactive view'}</div>
  </div>
{/if}
