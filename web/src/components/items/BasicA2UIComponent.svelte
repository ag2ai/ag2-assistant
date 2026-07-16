<script>
  import Icon from '../Icon.svelte'
  import BasicA2UIComponent from './BasicA2UIComponent.svelte'
  import { a2uiValue } from '../../lib/a2ui.js'

  let { component, components = [], data = {}, onDataChange = () => {}, onAction = () => {}, depth = 0 } = $props()
  // The component graph is agent-produced and children are resolved by id from a
  // flat list, so a cyclic (A→B→A) or self-referential graph would recurse without
  // bound and blow the stack. Cap the render depth — real layouts are shallow.
  const MAX_DEPTH = 24
  const type = $derived(((component && component.component) || 'Text').toLowerCase())
  const byId = $derived(new Map((components || []).filter((c) => c && c.id).map((c) => [c.id, c])))

  function list(value) {
    return Array.isArray(value) ? value.filter(Boolean) : []
  }

  function childIds(value) {
    return Array.isArray(value) ? value.filter((id) => typeof id === 'string') : []
  }

  function child(id) {
    return byId.get(id)
  }

  function storySummary(story) {
    return story.summary || story.detail || story.text || ''
  }

  const checkboxValue = $derived(!!a2uiValue(component?.value, data))
  const checkboxPath = $derived(
    component?.value && typeof component.value === 'object' ? component.value.path : ''
  )

  function toggleCheckbox(event) {
    if (checkboxPath) onDataChange(checkboxPath, event.currentTarget.checked)
  }

  const valuePath = $derived(
    component?.value && typeof component.value === 'object' ? component.value.path : ''
  )
  const inputValue = $derived(a2uiValue(component?.value, data) ?? '')
  const sliderStep = $derived(
    component?.steps ? (Number(component.max) - Number(component.min || 0)) / Number(component.steps) : undefined
  )
  const iconName = $derived(({ accountCircle: 'users', add: 'plus', arrowBack: 'chevron-left', arrowForward: 'chevron-right', attachFile: 'paperclip', calendarToday: 'clock', close: 'x', delete: 'trash', event: 'clock', favorite: 'thumbs-up', folder: 'folder', play: 'send', refresh: 'rotate-cw', send: 'send', settings: 'settings', stop: 'square', warning: 'alert-triangle' })[a2uiValue(component?.name, data)] || a2uiValue(component?.name, data))
  const videoUrl = $derived(a2uiValue(component?.url, data) || '')
  const youtubeEmbed = $derived(youtubeUrl(videoUrl))

  function setValue(event) {
    if (valuePath) onDataChange(valuePath, event.currentTarget.value)
  }

  function setNumber(event) {
    if (valuePath) onDataChange(valuePath, Number(event.currentTarget.value))
  }

  function setDateTime(event) {
    if (!valuePath) return
    const value = event.currentTarget.value
    onDataChange(valuePath, component.enableDate && component.enableTime && value ? new Date(value).toISOString() : value)
  }

  function toggleChoice(option, selected) {
    if (!valuePath) return
    const current = Array.isArray(inputValue) ? inputValue : []
    const next = component.variant === 'multipleSelection'
      ? (selected ? [...new Set([...current, option])] : current.filter((value) => value !== option))
      : [option]
    onDataChange(valuePath, next)
  }

  function youtubeUrl(url) {
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

  function actionContext(value) {
    if (Array.isArray(value)) return value.map(actionContext)
    if (value && typeof value === 'object') {
      if (typeof value.path === 'string' && Object.keys(value).length === 1) return a2uiValue(value, data)
      return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, actionContext(item)]))
    }
    return value
  }

  function clickButton() {
    const event = component?.action?.event
    if (!event?.name) return
    onAction({ name: event.name, sourceComponentId: component.id, context: actionContext(event.context || {}) })
  }
</script>

{#if depth >= MAX_DEPTH}
  <!-- cyclic or pathologically deep component graph — stop recursing -->
{:else if type === 'column'}
  <div class="a2ui-basic-col">
    {#each childIds(component.children) as id}
      {#if child(id)}<BasicA2UIComponent component={child(id)} {components} {data} {onDataChange} {onAction} depth={depth + 1} />{/if}
    {/each}
  </div>
{:else if type === 'row'}
  <div class="a2ui-basic-row">
    {#each childIds(component.children) as id}
      {#if child(id)}<BasicA2UIComponent component={child(id)} {components} {data} {onDataChange} {onAction} depth={depth + 1} />{/if}
    {/each}
  </div>
{:else if type === 'list'}
  <div class="a2ui-list">
    {#each childIds(component.children) as id}
      {#if child(id)}<BasicA2UIComponent component={child(id)} {components} {data} {onDataChange} {onAction} depth={depth + 1} />{/if}
    {/each}
  </div>
{:else if type === 'card'}
  <div class="a2ui-basic-card">
    {#if child(component.child)}<BasicA2UIComponent component={child(component.child)} {components} {data} {onDataChange} {onAction} depth={depth + 1} />{/if}
  </div>
{:else if type === 'text'}
  <div class:a2ui-main={component.variant && component.variant !== 'body'} class="a2ui-text">{a2uiValue(component.text, data) || ''}</div>
{:else if type === 'divider'}
  <div class="a2ui-divider" aria-hidden="true"></div>
{:else if type === 'checkbox'}
  <label class="a2ui-checkbox">
    <input type="checkbox" checked={checkboxValue} onchange={toggleCheckbox} />
    <span>{a2uiValue(component.label, data) || ''}</span>
  </label>
{:else if type === 'button'}
  <button class:primary={component.variant === 'primary'} class="a2ui-button" onclick={clickButton}>
    {a2uiValue(child(component.child)?.text, data) || 'Continue'}
  </button>
{:else if type === 'image'}
  <img class="a2ui-image {component.variant || ''}" src={a2uiValue(component.url, data)} alt={a2uiValue(component.description, data) || ''} style:object-fit={component.fit === 'scaleDown' ? 'scale-down' : component.fit || 'fill'} />
{:else if type === 'icon'}
  <span class="a2ui-icon" title={String(a2uiValue(component.name, data) || '')}><Icon name={iconName} size={22} /></span>
{:else if type === 'video'}
  {#if youtubeEmbed}
    <iframe class="a2ui-video" src={youtubeEmbed} title="Video" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe>
  {:else}
    <video class="a2ui-video" controls src={videoUrl} poster={a2uiValue(component.posterUrl, data) || undefined}></video>
  {/if}
{:else if type === 'textfield'}
  <label class="a2ui-field">
    <span>{a2uiValue(component.label, data) || ''}</span>
    {#if component.variant === 'longText'}
      <textarea value={inputValue} placeholder={a2uiValue(component.placeholder, data) || ''} oninput={setValue}></textarea>
    {:else}
      <input type={component.variant === 'number' ? 'number' : component.variant === 'obscured' ? 'password' : 'text'} value={inputValue} placeholder={a2uiValue(component.placeholder, data) || ''} oninput={setValue} />
    {/if}
  </label>
{:else if type === 'choicepicker'}
  <fieldset class="a2ui-choice">
    {#if component.label}<legend>{a2uiValue(component.label, data)}</legend>{/if}
    <div class:chips={component.displayStyle === 'chips'}>
      {#each list(component.options) as option}
        {@const selected = Array.isArray(inputValue) && inputValue.includes(option.value)}
        <label>
          <input type={component.variant === 'multipleSelection' ? 'checkbox' : 'radio'} name={component.id} checked={selected} onchange={(event) => toggleChoice(option.value, event.currentTarget.checked)} />
          <span>{a2uiValue(option.label, data) || option.value}</span>
        </label>
      {/each}
    </div>
  </fieldset>
{:else if type === 'slider'}
  <label class="a2ui-field a2ui-slider">
    {#if component.label}<span>{a2uiValue(component.label, data)}</span>{/if}
    <input type="range" min={component.min || 0} max={component.max} step={sliderStep} value={inputValue} oninput={setNumber} />
    <output>{inputValue}</output>
  </label>
{:else if type === 'datetimeinput'}
  <label class="a2ui-field">
    {#if component.label}<span>{a2uiValue(component.label, data)}</span>{/if}
    <input type={component.enableDate && component.enableTime ? 'datetime-local' : component.enableDate ? 'date' : 'time'} value={component.enableDate && component.enableTime && inputValue ? String(inputValue).slice(0, 16) : inputValue} min={a2uiValue(component.min, data) || undefined} max={a2uiValue(component.max, data) || undefined} onchange={setDateTime} />
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
      {#each list(component.rows) as row}
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
      {#each list(component.stories) as story}
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
