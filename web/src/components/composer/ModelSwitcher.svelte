<script lang="ts">
  // The composer's model switcher: it sets the open Chat's override (PATCH /chats/{id}
  // {model}), never the install-wide Active, which Settings → Models owns (ADR 0025).
  //
  // Closed, the button names the server-resolved `effective_model`; open, the check says
  // where that came from — "Use default" when inheriting, the config when chosen.
  //
  // On a Chat that does not exist yet the choice is held in `chatModel.pending` and
  // rides the first message. The switcher stays inert until the read says which it is.
  //
  // The model LIST comes from the shared `llmConfigs` store (lib/llm.ts, ADR 0004), so
  // Settings edits show up live. Presentation is the shared ModelSwitcherView.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api/index.ts'
  import { SETTINGS_PAGE, chatModel } from '../../store.ts'
  import { openOverlay } from '../../router.ts'
  import { isUsable, llmConfigs, loadLlmConfigs } from '../../lib/llm.ts'
  import { chooseModel, loadedChat, switcherSelection } from '../../lib/chatModel.ts'
  import { TYPE_LABEL } from '../../lib/providerLabels.ts'
  import ModelSwitcherView from '../ModelSwitcherView.svelte'

  let busy = $state(false)

  // A failed load just leaves the row empty-stated; the composer stays usable.
  onMount(() => { loadLlmConfigs().catch(() => {}) })

  const configs = $derived($llmConfigs.configs)
  const envOverride = $derived($llmConfigs.envOverride)
  const selection = $derived(switcherSelection($chatModel))
  // Derived (so they compare by value) — the reload effect below must fire on a NEW
  // chat or a NEW install-wide Active, never on its own write back into chatModel.
  const chatId = $derived($chatModel.chatId)
  const activeId = $derived($llmConfigs.active)
  // Inert until the read says whether this chat exists, which decides whether a pick
  // is a PATCH or a model riding the first message (ADR 0025).
  const loading = $derived($chatModel.exists === null)

  async function load(id: string) {
    try {
      const body = await api.chat(id)
      let patch: string | null = null
      chatModel.update((s) => {
        const next = loadedChat(s, id, body)
        patch = next.patch
        return next.state
      })
      // A pick held while `exists` was unknown, on a chat that turns out to exist, is
      // that chat's override and is written now rather than riding the next turn.
      if (patch !== null) await api.updateChat(id, { model: patch })
    } catch {
      // Keep what we have and stay inert if this was the first read; the effect below
      // re-reads on the next chat change or Active move.
    }
  }

  // Re-read on every chat change, and whenever the install-wide Active moves — a chat
  // that inherits follows it, so its effective model has just changed underneath us.
  $effect(() => {
    const id = chatId
    void activeId
    if (id) load(id)
  })

  async function choose(id: string) {
    const state = $chatModel
    if (busy || !state.chatId) return
    const next = chooseModel(state, id)
    chatModel.set(next.state)
    if (next.patch === null) return   // no chat yet — held for the first message
    busy = true
    try { await api.updateChat(state.chatId, { model: next.patch }) } catch { /* reload below corrects */ }
    await load(state.chatId)
    busy = false
  }

  const openSettings = () => openOverlay('settings', SETTINGS_PAGE.MODELS)
</script>

<ModelSwitcherView
  {configs} {envOverride} busy={busy || loading}
  activeId={selection.activeId} inherited={selection.inherited} closedBadges={false}
  title="Model for your next message in this chat"
  brandFor={(c) => c.type}
  labelFor={(c) => `${TYPE_LABEL[c.type]} · ${c.model}`}
  usable={isUsable}
  defaultEntry={{ label: 'Use default', sub: 'Follow the Active model' }}
  emptyLabel="No models configured — add one in Settings"
  onEmpty={openSettings}
  onChoose={(c) => choose(c.id)}
  onDefault={() => choose('')}
  onManage={openSettings}
/>
