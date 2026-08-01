<script>
  // AG2 Assistant — BrandMark: a third party's official logo, drawn from the one brand
  // lookup. Every surface that names a platform or a provider draws it through here,
  // so a mark looks the same in Settings as it does in a model switcher.
  //
  // Decoration only — it always sits beside the text label it illustrates, so it is
  // hidden from screen readers rather than announced a second time.
  //
  // Renders nothing for a key this build does not know; the caller decides what to put
  // in its place. See lib/brandMarks.js for why some brands carry a colour and some
  // are drawn in currentColor.
  import { brandMark } from '../lib/brandMarks.js'

  // brand: a platform id ('telegram', 'github', …) or an LLM/voice provider type.
  let { brand, size = 20 } = $props()

  const mark = $derived(brandMark(brand))
</script>

{#if mark}
  <svg
    xmlns="http://www.w3.org/2000/svg"
    width={size} height={size} viewBox={mark.viewBox || '0 0 24 24'}
    style="display:inline-block;flex:none;vertical-align:middle"
    aria-hidden="true"
  >
    {#if mark.kind === 'multi'}
      {#each mark.parts as part}<path d={part.path} fill={part.fill} />{/each}
    {:else}
      <path d={mark.path} fill={mark.fill || 'currentColor'} />
    {/if}
  </svg>
{/if}
