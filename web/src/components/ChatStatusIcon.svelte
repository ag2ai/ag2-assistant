<script lang="ts">
  import Icon from './Icon.svelte'
  import { CHAT_STATUS } from '../lib/chatActivity.ts'
  import type { ChatStatus } from '../schemas/chat.ts'
  let { status }: { status: ChatStatus | null } = $props()
</script>

{#if status}
  <span class="chat-status {status}" role="img" aria-label={CHAT_STATUS[status].label} title={CHAT_STATUS[status].label}>
    <Icon name={CHAT_STATUS[status].icon} size={17} />
  </span>
{/if}

<style>
  .chat-status { display: inline-flex; flex: none; align-items: center; justify-content: center; width: 18px; }
  .running { color: var(--info); }
  .completed { color: var(--success); }
  .error { color: var(--danger); }
  .waiting { color: var(--warning); }
  :global([data-animations='off']) .running :global(svg) { animation: none; }
  @media (prefers-reduced-motion: reduce) { .running :global(svg) { animation: none; } }
</style>
