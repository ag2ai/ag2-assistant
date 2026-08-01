<script>
  // AG2 Assistant — BrandMark: a third party's official logo, from lib/brandMarks.js.
  // Decoration, so hidden from screen readers; an unknown key draws nothing.
  import { brandMark } from '../lib/brandMarks.js'

  // brand: a platform id ('telegram', 'github', …) or an LLM/voice provider type.
  let { brand, size = 20 } = $props()

  const mark = $derived(brandMark(brand))

  // Gradient ids are document-wide, so each instance gets its own and keeps it.
  const gradientId = `brandmark-gradient-${++seq}`
</script>

<script module>
  let seq = 0
</script>

{#if mark}
  <svg
    xmlns="http://www.w3.org/2000/svg"
    class="brandmark"
    width={size} height={size} viewBox={mark.viewBox || '0 0 24 24'}
    aria-hidden="true"
  >
    {#if mark.kind === 'multi'}
      {#each mark.parts as part}<path d={part.path} fill={part.fill} />{/each}
    {:else if mark.kind === 'gradient'}
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          {#each mark.stops as stop, i}
            <stop offset={i / Math.max(1, mark.stops.length - 1)} stop-color={stop} />
          {/each}
        </linearGradient>
      </defs>
      <path d={mark.path} fill="url(#{gradientId})" />
    {:else}
      <path d={mark.path} fill={mark.fill || 'currentColor'} />
    {/if}
  </svg>
{/if}
