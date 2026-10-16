<script lang="ts">
  import Icon from '../Icon.svelte'
  import ToolFile from './ToolFile.svelte'
  import ToolFolder from './ToolFolder.svelte'
  import ToolExecution from './ToolExecution.svelte'
  import { chats } from '../../store.ts'
  import { go } from '../../router.ts'
  import { fmtClock, fmtStamp } from '../../lib/time.ts'
  import { groupToolRuns } from '../../lib/toolRuns.ts'
  import type { ToolRun } from '../../schemas/events.ts'

  let { label, runs }: { label: string; runs: ToolRun[] } = $props()
  const groups = $derived(groupToolRuns(runs))
  const hasDetails = $derived(runs.some(run => run.detail && run.detail !== label))
  const id = $props.id()
  let button: HTMLButtonElement | undefined = $state()
  let panel: HTMLDivElement | undefined = $state()
  let opened = $state(false)
  let pinned = false
  let restoringFocus = false
  let pointerFocus = false
  let timer: ReturnType<typeof setTimeout> | undefined
  let left = $state(12)
  let top = $state(12)

  function keepOpen() { clearTimeout(timer) }
  function open() { keepOpen(); opened = true }
  function close() { keepOpen(); pinned = false; opened = false }

  function scheduleClose() {
    keepOpen()
    timer = setTimeout(() => {
      if (pinned || panel?.matches(':hover') || button?.matches(':hover')) return
      if (!pointerFocus && document.activeElement?.matches(':focus-visible') &&
          (document.activeElement === button || panel?.contains(document.activeElement))) return
      close()
    }, 180)
  }

  function position() {
    if (!panel || !button) return
    const anchor = button.getBoundingClientRect()
    if (anchor.bottom < 0 || anchor.top > window.innerHeight) { close(); return }
    const size = panel.getBoundingClientRect()
    left = Math.max(12, Math.min(anchor.left, window.innerWidth - size.width - 12))
    const below = anchor.bottom + 6
    const next = below + size.height <= window.innerHeight - 12 ? below : anchor.top - size.height - 6
    top = Math.max(12, Math.min(next, window.innerHeight - size.height - 12))
  }

  $effect(() => {
    if (!panel) return
    if (opened) {
      if (!panel.matches(':popover-open')) panel.showPopover()
      position()
    } else if (panel.matches(':popover-open')) panel.hidePopover()
  })

  $effect(() => {
    if (!opened) return
    window.addEventListener('resize', position)
    window.addEventListener('scroll', position, true)
    const observer = new ResizeObserver(position)
    if (panel) observer.observe(panel)
    return () => {
      window.removeEventListener('resize', position)
      window.removeEventListener('scroll', position, true)
      observer.disconnect()
    }
  })
  $effect(() => () => keepOpen())
</script>

{#if hasDetails}
<button bind:this={button} type="button" class="chip tool-trigger"
  popovertarget={id}
  aria-haspopup="dialog" aria-expanded={opened} aria-controls={id}
  onpointerenter={(event) => { if (event.pointerType !== 'touch') open() }}
  onpointerdown={() => pointerFocus = true} onkeydown={() => pointerFocus = false}
  onpointerleave={scheduleClose} onfocus={() => { if (button?.matches(':focus-visible')) pointerFocus = false; if (!restoringFocus) open() }} onblur={scheduleClose}
  onclick={(event) => { event.preventDefault(); if (pinned) close(); else { pinned = true; open() } }}>
  <Icon name="zap" size={12} /> {label}{runs.length > 1 ? ' ×' + runs.length : ''}
</button>
<div bind:this={panel} {id} class="tool-runs" popover="auto" role="dialog" tabindex="-1" aria-label="{label} runs"
  style:left="{left}px" style:top="{top}px"
  onpointerenter={keepOpen} onpointerleave={scheduleClose} onfocusin={keepOpen} onfocusout={scheduleClose}
  onpointerdown={() => pointerFocus = true} onkeydown={() => pointerFocus = false}
  onbeforetoggle={(event) => {
    if (event.newState === 'closed' && panel?.contains(document.activeElement)) {
      restoringFocus = true; button?.focus(); restoringFocus = false
    }
  }}
  ontoggle={(event) => { opened = event.newState === 'open'; if (!opened) { pinned = false; keepOpen() } }}>
  <div class="runs-header">
    <h2>{label}{#if runs.length > 1}<span> ×{runs.length}</span>{/if}</h2>
  </div>
  <ol>
    {#each groups as { run, count, firstAt, lastAt } (run.id)}
      <li>
        <div class="run-time">
          {#if firstAt !== undefined}
            <time title={fmtStamp(firstAt) + (lastAt !== undefined && lastAt !== firstAt ? ' – ' + fmtStamp(lastAt) : '')}>
              <span>{fmtClock(firstAt)}</span>
              {#if lastAt !== undefined && fmtClock(lastAt) !== fmtClock(firstAt)}<span>– {fmtClock(lastAt)}</span>{/if}
            </time>
          {/if}
        </div>
        <div class="run-entry">
          <div class="run-heading">
            {#if run.card?.kind === 'file'}
              <ToolFile file={run.card} {count} onopen={close} />
            {:else if run.card?.kind === 'folder'}
              <ToolFolder path={run.card.path} {count} onopen={close} />
            {:else if run.card?.kind === 'execution'}
              {#if opened}<ToolExecution text={run.card.text} language={run.card.language} {count} />{/if}
            {:else if run.card?.kind === 'chat'}
              {@const chatId = run.card.chatId}
              {@const chat = $chats.find(chat => chat.chat_id === chatId)}
              {@const title = chat?.title || chat?.preview || chatId}
              <p><button type="button" class="chat-name" aria-label="Open chat {title}" onclick={() => { go('/c/' + encodeURIComponent(chatId)); close() }}>{title}</button>{#if count > 1}<span class="run-count"> ×{count}</span>{/if}
                {#if chat?.title || chat?.preview}<span class="chat-id">{run.card.chatId}</span>{/if}
              </p>
            {:else}
              <p>{run.detail}{#if count > 1}<span class="run-count"> ×{count}</span>{/if}</p>
            {/if}
          </div>
        </div>
      </li>
    {/each}
  </ol>
</div>
{:else}
  <span class="chip"><Icon name="zap" size={12} /> {label}{runs.length > 1 ? ' ×' + runs.length : ''}</span>
{/if}

<style>
  .tool-trigger { font-family: inherit; line-height: inherit; cursor: pointer; }
  .tool-trigger:hover, .tool-trigger[aria-expanded="true"] { color: var(--ink); border-color: var(--line-strong); }
  .tool-trigger:focus-visible { outline: none; box-shadow: var(--focus-ring); }
  .tool-runs { position: fixed; inset: auto; margin: 0; box-sizing: border-box; width: min(400px, calc(100vw - 24px)); max-height: min(420px, calc(100dvh - 24px)); overflow: auto; overscroll-behavior: contain; padding: 12px; border: 1px solid var(--line); border-radius: 12px; color: var(--ink); background: var(--panel); box-shadow: 0 12px 32px #0004; }
  .runs-header { position: sticky; top: 0; padding: 4px 0; background: var(--panel); }
  h2 { margin: 0; font-size: 12px; font-weight: var(--fw-semibold); }
  h2 span { margin-left: 4px; color: var(--muted); font-weight: normal; }
  ol { margin: 8px 0 0; padding: 0; list-style: none; }
  li { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-top: 1px solid var(--line); }
  .run-time { flex: none; width: 60px; color: var(--muted); font-size: 10px; }
  .run-entry { flex: 1; min-width: 0; }
  .run-heading { display: flex; align-items: center; gap: 8px; }
  p { min-width: 0; flex: 1; margin: 0; font-size: 12px; line-height: 1.45; white-space: pre-wrap; overflow-wrap: anywhere; }
  .run-count { color: var(--muted); white-space: nowrap; }
  .chat-id { display: block; color: var(--muted); font-size: 11px; }
  .chat-name { max-width: 100%; border: none; background: none; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-align: left; text-decoration: underline; text-underline-offset: 2px; overflow-wrap: anywhere; }
  .chat-name:focus-visible { outline: none; box-shadow: var(--focus-ring); border-radius: 2px; }
  time span { display: block; overflow-wrap: anywhere; }
</style>
