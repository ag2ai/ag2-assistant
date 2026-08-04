<script lang="ts">
  // One integration's status line — the same words and the same glyph wherever it is
  // read (list row, detail header). `status` comes from lib/integrations.ts.
  import Icon from '../Icon.svelte'
  import type { IntegrationStatus as Status } from '../../lib/integrations.ts'

  let { status }: { status: Status } = $props()
</script>

<span class="cnstat {status.kind}">
  {#if status.kind === 'ok'}<Icon name="check" size={13} />{/if}
  {#if status.kind === 'err'}<Icon name="alert-triangle" size={13} />{/if}
  <span>{status.text}</span>
</span>
