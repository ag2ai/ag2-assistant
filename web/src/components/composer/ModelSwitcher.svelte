<script>
  // The composer's model switcher: it sets the OPEN CHAT'S Text model, not the whole
  // install (ADR 0025). Choosing writes this chat's Chat override (PATCH /chats/{id}
  // {model}); "Use default" clears it with the empty string and the chat goes back to
  // inheriting whatever is Active. The install-wide Active is changed in Settings →
  // Models, where the "Use" action lives — nothing here touches it.
  //
  // Closed, the button names the model your next message will actually run on: the
  // server resolves the whole chain (env pin > Chat override > Task model > profile
  // Active override > install-wide Active) into `effective_model`, so this component
  // never computes it. Open, the check tells you WHERE that came from — on "Use
  // default" when the chat inherits, on the config when the chat has chosen.
  //
  // On a chat that does not exist yet the switcher is fully live: there is nothing to
  // PATCH, so the choice is held in `chatModel.pending` and rides the first message
  // (controller.send), which the server records as the new chat's override. That held
  // choice is deliberately page-local — a reload returns to inheriting.
  //
  // "Does not exist yet" is a fact only the read can supply, so the switcher stays
  // inert until it lands: a pick made against a guessed answer on a chat that DOES
  // exist would ride the turn instead of patching, and the server drops a model on a
  // chat that already has a transcript — the pick would vanish with no error. Anything
  // held while it was unknown is reconciled into a PATCH when the read lands.
  //
  // The model LIST still comes from the shared install-wide `llmConfigs` store
  // (lib/llm.js, ADR 0004), so an add/rename/delete in Settings → Models shows up here
  // live. Presentation is the shared ModelSwitcherView.
  import { onMount } from 'svelte'
  import { api } from '../../transport/api.js'
  import { SETTINGS_PAGE, chatModel } from '../../store.js'
  import { openOverlay } from '../../router.js'
  import { isUsable, llmConfigs, loadLlmConfigs } from '../../lib/llm.js'
  import { chooseModel, loadedChat, switcherSelection } from '../../lib/chatModel.js'
  import { TYPE_LABEL } from '../../lib/providerLabels.js'
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
  // Until the read lands we do not know whether this chat exists, and the answer
  // decides whether a pick is a PATCH or a model riding the first message. Rather
  // than guess, the switcher is inert for that moment (ADR 0025).
  const loading = $derived($chatModel.exists === null)

  async function load(id) {
    try {
      const body = await api.chat(id)
      let patch = null
      chatModel.update((s) => {
        const next = loadedChat(s, id, body)
        patch = next.patch
        return next.state
      })
      // A pick made while `exists` was unknown, on a chat that turns out to exist: it
      // is that chat's override and is written now. Riding the next turn instead would
      // lose it — the server ignores a model on a chat that already has a transcript.
      if (patch !== null) await api.updateChat(id, { model: patch })
    } catch {
      // Keep what we have; the switcher shows no model. If this was the FIRST read it
      // also stays inert, because "does this chat exist" is still unanswered and a
      // pick made on a guess is a pick that can be silently dropped. The effect below
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

  async function choose(id) {
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
  activeId={selection.activeId} inherited={selection.inherited} inheritedTag={false}
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
