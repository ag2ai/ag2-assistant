<script lang="ts">
  import Icon from '../Icon.svelte'
  import BasicA2UIComponent from './BasicA2UIComponent.svelte'
  import WeatherCard from './WeatherCard.svelte'
  import NewsWire from './NewsWire.svelte'
  import MarketBoard from './MarketBoard.svelte'
  import DecisionMatrix from './DecisionMatrix.svelte'
  import TaskProgress from './TaskProgress.svelte'
  import AgendaCard from './AgendaCard.svelte'
  import InboxBrief from './InboxBrief.svelte'
  import CodingSession from './CodingSession.svelte'
  import A2UIComposing from './A2UIComposing.svelte'
  import { a2uiComposingSurfaceId, isGenericSurfaceTitle, rows, str, withA2UIValue } from '../../lib/a2ui.ts'
  import type { A2UIAction, A2UIData, NewsStory, PlaceResult } from '../../lib/a2ui.ts'
  import { a2uiAction } from '../../controller.ts'
  import { thread } from '../../store.ts'
  import type { ThreadItem } from '../../schemas/events.ts'
  import { m } from '../../paraglide/messages.js'

  type Props = { item: Extract<ThreadItem, { kind: 'a2ui' }> }
  let { item }: Props = $props()
  const data = $derived(item.data || {})
  const components = $derived(item.components || item.component._components || [item.component])
  const type = $derived((item.component.component || 'AnswerBrief').toLowerCase())
  const isBasicLayout = $derived(['column', 'row', 'list', 'card', 'text', 'divider', 'checkbox', 'button', 'image', 'icon', 'video', 'textfield', 'choicepicker', 'slider', 'datetimeinput'].includes(type))
  const componentIcon = $derived(
    isBasicLayout ? 'sparkles'
      : type === 'weatherpanel' ? 'sun'
      : type === 'taskplan' || type === 'checklist' ? 'list'
      : type === 'newsdigest' ? 'globe'
      : type === 'marketboard' ? 'trending-up'
      : type === 'decisionmatrix' ? 'check'
      : type === 'taskprogress' ? 'clock'
      : type === 'agendacard' ? 'clock'
      : type === 'inboxbrief' ? 'globe'
      : type === 'restaurantfinder' ? 'search'
      : 'sparkles'
  )
  // The component type is the payload's own value; only the eyebrow localizes.
  const eyebrow = $derived(
    isBasicLayout ? m.a2ui_overview()
      : type === 'weatherpanel' ? m.a2ui_live_forecast()
      : type === 'taskplan' ? m.a2ui_task_plan()
      : type === 'newsdigest' ? m.a2ui_news_brief()
      : type === 'marketboard' ? m.a2ui_markets()
      : type === 'decisionmatrix' ? m.a2ui_decision()
      : type === 'taskprogress' ? m.a2ui_task_status()
      : type === 'agendacard' ? m.a2ui_agenda()
      : type === 'inboxbrief' ? m.a2ui_inbox()
      : type === 'restaurantfinder' ? m.a2ui_places()
      : 'A2UI'
  )
  const displayTitle = $derived(item.title === 'Briefing' ? m.a2ui_interactive_view() : item.title || eyebrow)
  const isComposingUpdate = $derived($thread.items.some(
    (entry) => entry.kind === 'agent' && entry.streaming && a2uiComposingSurfaceId(entry.text) === item.surfaceId
  ))
  const actionPending = $derived($thread.items.some(
    (entry) => entry.kind === 'note' && entry.a2uiActionPending && entry.surfaceId === item.surfaceId
  ))
  let inputData: A2UIData = $state({})

  $effect(() => {
    inputData = data
  })

  function setInputValue(path: string, value: unknown) {
    inputData = withA2UIValue(inputData, path, value)
  }

  function submitAction(action: A2UIAction) {
    a2uiAction({
      version: item.version || 'v1.0',
      action: { ...action, surfaceId: item.surfaceId, timestamp: new Date().toISOString() },
    })
  }

  function list<T>(value: unknown): T[] {
    return rows<T>(value)
  }

  function storySummary(story: NewsStory): string {
    return story.summary || story.detail || story.text || ''
  }

  // Agent-supplied text, so the sentinels stay English: the payload is server data and
  // is never translated (ADR 0031). Our own surface title uses isGenericSurfaceTitle.
  function genericText(value: unknown) {
    return ['structured answer', 'structured response', 'a2ui', ''].includes(String(value || '').toLowerCase())
  }

  const emptyAnswerBrief = $derived(
    !['column', 'row', 'list', 'card', 'text', 'divider', 'checkbox', 'button', 'image', 'icon', 'video', 'textfield', 'choicepicker', 'slider', 'datetimeinput', 'weatherpanel', 'taskplan', 'newsdigest', 'marketboard', 'decisionmatrix', 'taskprogress', 'agendacard', 'inboxbrief', 'restaurantfinder', 'checklist', 'codingsession'].includes(type) &&
    !list(data.sections).length &&
    genericText(data.topic) &&
    genericText(data.title) &&
    isGenericSurfaceTitle(item.title)
  )
</script>

{#if isComposingUpdate}
  <A2UIComposing />
{:else if !emptyAnswerBrief}
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
{:else if type === 'inboxbrief' && list(data.threads).length}
  <InboxBrief {data} />
{:else if type === 'codingsession'}
  <CodingSession {data} />
{:else}
<div class="a2ui">
  <div class="a2ui-head">
    <span class="a2ui-mark"><Icon name={componentIcon} size={15} /></span>
    <span class="a2ui-headtext">
      <span class="a2ui-eyebrow">{eyebrow}</span>
      <span class="a2ui-title">{displayTitle}</span>
    </span>
    <span class="a2ui-catalog" title={item.catalogId}>{m.a2ui_catalog()}</span>
  </div>

  {#if isBasicLayout}
    <BasicA2UIComponent component={item.component} {components} data={inputData} onDataChange={setInputValue} onAction={submitAction} />
  {:else if type === 'taskplan'}
    <div class="a2ui-task">
      <div class="a2ui-main">{str(data.objective) || m.drawer_new_task()}</div>
      <div class="a2ui-meta">
        <span><Icon name="clock" size={12} /> {str(data.cadence) || m.a2ui_tbc()}</span>
        <span><Icon name="sparkles" size={12} /> {m.a2ui_assistant_plan()}</span>
      </div>
      <div class="a2ui-cols">
        <section>
          <div class="a2ui-label">{m.a2ui_deliverables()}</div>
          {#each list<string>(data.deliverables) as row}
            <div class="a2ui-row"><Icon name="check" size={12} /> {row}</div>
          {/each}
        </section>
        <section>
          <div class="a2ui-label">{m.a2ui_next()}</div>
          {#each list<string>(data.nextSteps) as row}
            <div class="a2ui-row"><Icon name="chevron-right" size={12} /> {row}</div>
          {/each}
        </section>
      </div>
    </div>
  {:else if type === 'newsdigest'}
    <div class="a2ui-main">{str(data.topic) || m.a2ui_latest_news()}</div>
    <div class="a2ui-list">
      {#each list<NewsStory>(data.stories) as story}
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
    <div class="a2ui-main">{str(data.query) || m.a2ui_restaurants()}</div>
    <div class="a2ui-pills">
      {#each list<string>(data.filters) as filter}<span>{filter}</span>{/each}
    </div>
    <div class="a2ui-list">
      {#each list<PlaceResult>(data.results) as result}
        <div class="a2ui-story">
          <span><Icon name="search" size={13} /></span>
          <div><strong>{result.name}</strong><small>{result.detail}</small></div>
        </div>
      {/each}
    </div>
  {:else if type === 'checklist'}
    <div class="a2ui-main">{str(data.title) || item.title || m.a2ui_checklist()}</div>
    <div class="a2ui-list">
      {#each list<string>(data.items) as row}
        <div class="a2ui-story">
          <span><Icon name="check" size={13} /></span>
          <div><strong>{row}</strong></div>
        </div>
      {/each}
    </div>
  {:else}
    <div class="a2ui-main">{str(data.topic) || item.title || m.a2ui_structured_response()}</div>
    <div class="a2ui-pills">
      {#each list<string>(data.sections) as section}<span>{section}</span>{/each}
    </div>
  {/if}
</div>
{/if}
{/if}
{#if actionPending}
  <div class="a2ui-action-pending" role="status" aria-label={m.a2ui_submitting()}><Icon name="rotate-cw" size={14} /></div>
{/if}
