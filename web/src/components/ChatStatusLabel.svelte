<script lang="ts">
  import ChatStatusIcon from './ChatStatusIcon.svelte'
  import { CHAT_STATUS } from '../lib/chatActivity.ts'
  import { chatStatusTime } from '../lib/chatStatusTime.ts'
  import type { ChatSeen } from '../schemas/chat.ts'
  let { chat, now }: { chat: ChatSeen; now: number } = $props()
  const time = $derived(chatStatusTime(chat, now))
  const description = $derived(chat.status ? `${CHAT_STATUS[chat.status].label} · ${time}${chat.status === 'waiting' ? ' remaining' : ''}` : '')
</script>

{#if chat.status}
  <span class="chat-status-label {chat.status}" title={description} aria-label={description}>
    <span class="status-time">{time}</span>
    <ChatStatusIcon status={chat.status} />
  </span>
{/if}

<style>
  .chat-status-label { flex: none; display: inline-flex; align-items: center; gap: 4px; font-size: 11px; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .running { color: var(--info); }
  .completed { color: var(--success); }
  .error { color: var(--danger); }
  .waiting { color: var(--warning); }
</style>
