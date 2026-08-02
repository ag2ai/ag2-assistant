<script>
  // Minimal transient toast for profile-recovery (§4.9). Fixed bottom-centre,
  // token-styled, no queue — shows the single `notice` store value when set.
  // Kept deliberately tiny: it flashes for ~1.5s before the client re-resolves
  // to a valid profile (a full-page nav clears it).
  import { notice } from '../store.ts'
  import Icon from './Icon.svelte'
</script>

{#if $notice}
  <div class="notice ag2-rise" role="status" aria-live="polite">
    <span class="ntc-icon"><Icon name="clock" size={15} /></span>
    <span class="ntc-text">{$notice.text}</span>
  </div>
{/if}

<style>
  .notice {
    position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%);
    z-index: var(--z-toast, 9999);
    display: inline-flex; align-items: center; gap: 9px;
    max-width: min(92vw, 460px);
    padding: 11px 16px;
    background: var(--surface); color: var(--text);
    border: 1px solid var(--line); border-radius: var(--radius-md, 10px);
    box-shadow: var(--shadow-lg);
    font-family: var(--font-sans); font-size: var(--text-sm);
  }
  .ntc-icon { color: var(--accent); display: inline-flex; flex: none; }
  .ntc-text { line-height: var(--leading-normal); }
</style>
