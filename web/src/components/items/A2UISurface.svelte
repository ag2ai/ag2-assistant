<script>
  import Icon from '../Icon.svelte'
  import BasicA2UIComponent from './BasicA2UIComponent.svelte'
  import WeatherCard from './WeatherCard.svelte'
  import NewsWire from './NewsWire.svelte'
  import MarketBoard from './MarketBoard.svelte'
  import DecisionMatrix from './DecisionMatrix.svelte'
  import TaskProgress from './TaskProgress.svelte'
  import AgendaCard from './AgendaCard.svelte'

  let { item } = $props()
  const data = $derived(item.data || {})
  const components = $derived(item.components || item.component?._components || [item.component].filter(Boolean))
  const type = $derived(((item.component && item.component.component) || 'AnswerBrief').toLowerCase())
  const isBasicLayout = $derived(['column', 'row', 'list', 'card', 'text', 'divider'].includes(type))
  const componentIcon = $derived(
    isBasicLayout ? 'sparkles'
      : type === 'weatherpanel' ? 'sun'
      : type === 'taskplan' || type === 'checklist' ? 'list'
      : type === 'newsdigest' ? 'globe'
      : type === 'marketboard' ? 'trending-up'
      : type === 'decisionmatrix' ? 'check'
      : type === 'taskprogress' ? 'clock'
      : type === 'agendacard' ? 'clock'
      : type === 'restaurantfinder' ? 'search'
      : 'sparkles'
  )
  const eyebrow = $derived(
    isBasicLayout ? 'Overview'
      : type === 'weatherpanel' ? 'Live forecast'
      : type === 'taskplan' ? 'Task plan'
      : type === 'newsdigest' ? 'News brief'
      : type === 'marketboard' ? 'Markets'
      : type === 'decisionmatrix' ? 'Decision'
      : type === 'taskprogress' ? 'Task status'
      : type === 'agendacard' ? 'Agenda'
      : type === 'restaurantfinder' ? 'Places'
      : 'A2UI'
  )
  const displayTitle = $derived(item.title || eyebrow)

  function list(value) {
    return Array.isArray(value) ? value.filter(Boolean) : []
  }

  function storySummary(story) {
    return story.summary || story.detail || story.text || ''
  }

  function genericText(value) {
    return ['structured answer', 'structured response', 'a2ui', ''].includes(String(value || '').toLowerCase())
  }

  const emptyAnswerBrief = $derived(
    !['column', 'row', 'list', 'card', 'text', 'divider', 'weatherpanel', 'taskplan', 'newsdigest', 'marketboard', 'decisionmatrix', 'taskprogress', 'agendacard', 'restaurantfinder', 'checklist'].includes(type) &&
    !list(data.sections).length &&
    genericText(data.topic) &&
    genericText(data.title) &&
    genericText(item.title)
  )
</script>

{#if !emptyAnswerBrief}
{#if type === 'newsdigest' && list(data.stories).length}
  <NewsWire {data} />
{:else if type === 'weatherpanel'}
  <WeatherCard {data} />
{:else if type === 'marketboard' && list(data.quotes).length}
  <MarketBoard {data} />
{:else if type === 'decisionmatrix' && list(data.options).length}
  <DecisionMatrix {data} />
{:else if type === 'taskprogress' && list(data.tasks).length}
  <TaskProgress {data} />
{:else if type === 'agendacard'}
  <AgendaCard {data} />
{:else}
<div class="a2ui">
  <div class="a2ui-head">
    <span class="a2ui-mark"><Icon name={componentIcon} size={15} /></span>
    <span class="a2ui-headtext">
      <span class="a2ui-eyebrow">{eyebrow}</span>
      <span class="a2ui-title">{displayTitle}</span>
    </span>
    <span class="a2ui-catalog" title={item.catalogId}>AG2 catalog</span>
  </div>

  {#if isBasicLayout}
    <BasicA2UIComponent component={item.component} {components} />
  {:else if type === 'taskplan'}
    <div class="a2ui-task">
      <div class="a2ui-main">{data.objective || 'New task'}</div>
      <div class="a2ui-meta">
        <span><Icon name="clock" size={12} /> {data.cadence || 'To be confirmed'}</span>
        <span><Icon name="sparkles" size={12} /> Assistant plan</span>
      </div>
      <div class="a2ui-cols">
        <section>
          <div class="a2ui-label">Deliverables</div>
          {#each list(data.deliverables) as row}
            <div class="a2ui-row"><Icon name="check" size={12} /> {row}</div>
          {/each}
        </section>
        <section>
          <div class="a2ui-label">Next</div>
          {#each list(data.nextSteps) as row}
            <div class="a2ui-row"><Icon name="chevron-right" size={12} /> {row}</div>
          {/each}
        </section>
      </div>
    </div>
  {:else if type === 'newsdigest'}
    <div class="a2ui-main">{data.topic || 'Latest news'}</div>
    <div class="a2ui-list">
      {#each list(data.stories) as story}
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
  {:else if type === 'restaurantfinder'}
    <div class="a2ui-main">{data.query || 'Restaurants'}</div>
    <div class="a2ui-pills">
      {#each list(data.filters) as filter}<span>{filter}</span>{/each}
    </div>
    <div class="a2ui-list">
      {#each list(data.results) as result}
        <div class="a2ui-story">
          <span><Icon name="search" size={13} /></span>
          <div><strong>{result.name}</strong><small>{result.detail}</small></div>
        </div>
      {/each}
    </div>
  {:else if type === 'checklist'}
    <div class="a2ui-main">{data.title || item.title || 'Checklist'}</div>
    <div class="a2ui-list">
      {#each list(data.items) as row}
        <div class="a2ui-story">
          <span><Icon name="check" size={13} /></span>
          <div><strong>{row}</strong></div>
        </div>
      {/each}
    </div>
  {:else}
    <div class="a2ui-main">{data.topic || item.title || 'Structured response'}</div>
    <div class="a2ui-pills">
      {#each list(data.sections) as section}<span>{section}</span>{/each}
    </div>
  {/if}
</div>
{/if}
{/if}
