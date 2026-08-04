<script lang="ts">
  // The shared top app bar (.mhead). Every main-column page renders one so the
  // header — back button, title/subtitle, and the SystemHealth / ThemeToggle / AG2
  // right-side actions — reads identically across chat, run, task and placeholders.
  // Styling lives globally in app.css (.mhead …); this component owns only markup.
  import type { Snippet } from 'svelte'
  import { ag2View } from '../store.ts'
  import { toggleAsideInspector } from '../router.ts'
  import Icon from './Icon.svelte'
  import ThemeToggle from './ThemeToggle.svelte'
  import SystemHealth from './SystemHealth.svelte'

  // back: optional { label, onClick } for the left chevron button.
  // children: optional snippet rendered between the titles and the actions (e.g. a status badge).
  type Props = {
    back?: { label: string; onClick: () => void } | null
    title?: string
    subtitle?: string
    children?: Snippet
  }

  let { back = null, title = '', subtitle = '', children }: Props = $props()
</script>

<div class="mhead">
  {#if back}
    <button class="back" onclick={back.onClick}><Icon name="chevron-left" size={15} /> {back.label}</button>
  {/if}
  <span class="titles">
    <span class="title">{title}</span>
    {#if subtitle}<span class="msub">{subtitle}</span>{/if}
  </span>
  {@render children?.()}
  <div class="hactions">
    <SystemHealth />
    <ThemeToggle />
    <button class="ag2toggle" class:on={$ag2View} class:ag2-glow={$ag2View} onclick={toggleAsideInspector}
            title="AG2 view — reveal the live AG2 events powering the UI"><Icon name="code" size={14} /> AG2</button>
  </div>
</div>
