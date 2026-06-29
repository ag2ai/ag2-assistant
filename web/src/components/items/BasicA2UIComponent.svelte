<script>
  import Icon from '../Icon.svelte'
  import BasicA2UIComponent from './BasicA2UIComponent.svelte'

  let { component, components = [] } = $props()
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
</script>

{#if type === 'column'}
  <div class="a2ui-basic-col">
    {#each childIds(component.children) as id}
      {#if child(id)}<BasicA2UIComponent component={child(id)} {components} />{/if}
    {/each}
  </div>
{:else if type === 'row'}
  <div class="a2ui-basic-row">
    {#each childIds(component.children) as id}
      {#if child(id)}<BasicA2UIComponent component={child(id)} {components} />{/if}
    {/each}
  </div>
{:else if type === 'list'}
  <div class="a2ui-list">
    {#each childIds(component.children) as id}
      {#if child(id)}<BasicA2UIComponent component={child(id)} {components} />{/if}
    {/each}
  </div>
{:else if type === 'card'}
  <div class="a2ui-basic-card">
    {#if child(component.child)}<BasicA2UIComponent component={child(component.child)} {components} />{/if}
  </div>
{:else if type === 'text'}
  <div class:a2ui-main={component.variant && component.variant !== 'body'} class="a2ui-text">{component.text || ''}</div>
{:else if type === 'divider'}
  <div class="a2ui-divider" aria-hidden="true"></div>
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
    <div class="a2ui-main">{component.title || component.topic || component.component || 'Briefing'}</div>
  </div>
{/if}
