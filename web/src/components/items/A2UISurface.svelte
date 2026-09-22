<script lang="ts">
  import Icon from '../Icon.svelte'
  import BasicA2UIComponent from './BasicA2UIComponent.svelte'
  import CodingSession from './CodingSession.svelte'
  import A2UIComposing from './A2UIComposing.svelte'
  import { a2uiComposingSurfaceId, rows, str, withA2UIValue } from '../../lib/a2ui.ts'
  import type { A2UIAction, A2UIData } from '../../lib/a2ui.ts'
  import { a2uiAction } from '../../controller.ts'
  import { thread } from '../../store.ts'
  import type { ThreadItem } from '../../schemas/events.ts'

  type Props = { item: Extract<ThreadItem, { kind: 'a2ui' }> }
  let { item }: Props = $props()
  const data = $derived(item.data || {})
  const components = $derived(item.components || item.component._components || [item.component])
  const type = $derived((item.component.component || 'AnswerBrief').toLowerCase())
  // The layout primitives a surface can be rooted at — anything else is a Card
  // type the renderer still knows, or an answer with no shape at all.
  const LAYOUT = ['column', 'row', 'list', 'card', 'text', 'divider', 'checkbox', 'button', 'image', 'icon', 'video', 'textfield', 'choicepicker', 'slider', 'datetimeinput']
  const isBasicLayout = $derived(LAYOUT.includes(type))
  // A feature Card draws its own frame and heading; the generic chrome is skipped.
  const isFeature = $derived(type === 'card' && str(item.component.variant) === 'feature')
  const eyebrow = $derived(isBasicLayout ? 'Overview' : 'A2UI')
  const displayTitle = $derived(item.title === 'Briefing' ? 'Interactive view' : item.title || eyebrow)
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

  function genericText(value: unknown) {
    return ['structured answer', 'structured response', 'a2ui', ''].includes(String(value || '').toLowerCase())
  }

  const emptyAnswerBrief = $derived(
    ![...LAYOUT, 'codingsession'].includes(type) &&
    !list(data.sections).length &&
    genericText(data.topic) &&
    genericText(data.title) &&
    genericText(item.title)
  )
</script>

{#if isComposingUpdate}
  <A2UIComposing />
{:else if isFeature}
  <BasicA2UIComponent component={item.component} {components} data={inputData} onDataChange={setInputValue} onAction={submitAction} />
{:else if !emptyAnswerBrief}
{#if type === 'codingsession'}
  <CodingSession {data} />
{:else}
<div class="a2ui">
  <div class="a2ui-head">
    <span class="a2ui-mark"><Icon name="sparkles" size={15} /></span>
    <span class="a2ui-headtext">
      <span class="a2ui-eyebrow">{eyebrow}</span>
      <span class="a2ui-title">{displayTitle}</span>
    </span>
    <span class="a2ui-catalog" title={item.catalogId}>AG2 catalog</span>
  </div>

  {#if isBasicLayout}
    <BasicA2UIComponent component={item.component} {components} data={inputData} onDataChange={setInputValue} onAction={submitAction} />
  {:else}
    <div class="a2ui-main">{str(data.topic) || item.title || 'Structured response'}</div>
    <div class="a2ui-pills">
      {#each list<string>(data.sections) as section}<span class="a2ui-text a2ui-tone-muted a2ui-t-pill">{section}</span>{/each}
    </div>
  {/if}
</div>
{/if}
{/if}
{#if actionPending}
  <div class="a2ui-action-pending" role="status" aria-label="Submitting action"><Icon name="rotate-cw" size={14} /></div>
{/if}
