<script lang="ts">
  // One platform's mark, wherever Settings → Integrations names a platform. An
  // unrecognised platform falls back to a square carrying the name's first letter.
  import BrandMark from '../BrandMark.svelte'
  import { brandMark } from '../../lib/brandMarks.ts'

  // platform: a CATALOG id. name: the Connection's own name, only read when the
  // platform is unrecognised. sm: the smaller size the Add grid draws marks at.
  type Props = { platform: string; name?: string; sm?: boolean }
  let { platform, name = '', sm = false }: Props = $props()

  const known = $derived(brandMark(platform) !== null)
</script>

{#if known}
  <BrandMark brand={platform} size={sm ? 16 : 20} />
{:else}
  <span class="cnmark" class:sm style="--tint:var(--muted)">{((name || '').trim()[0] || '?').toUpperCase()}</span>
{/if}
