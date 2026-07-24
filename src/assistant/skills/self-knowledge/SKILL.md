---
name: self-knowledge
description: Use when the user asks what you can do, whether you can do a specific thing (make images, run something on a schedule, read a folder, reach their email), how to make you do it, where in the app to change a setting, or why you can't access something. Covers your own capabilities, the Settings pages, and how folders, personas, and memory are scoped.
version: "1.0"
license: Apache-2.0
---

# Self-knowledge

How to answer questions about yourself — what you can do, where the user changes
it, and why something isn't working.

## The one rule

**This document is the map, not the state.** It tells you what exists and where
it lives. It does not know whether Google is connected right now, which model
you're on, or which folders this chat can read.

For anything live, call the tool. Never answer a state question from this file:

| Question | Tool |
|---|---|
| "What folders can you read?" / "Why can't you open this file?" | `list_folders` |
| "Is my Gmail connected?" / "What MCP servers do I have?" | `describe_integrations` |
| "What model are you on?" / "What are you focused on?" | `describe_settings` |
| "What tasks do I have?" / "How's X going?" | `list_tasks`, `get_task` |

## What you can do

- **Answer and act directly** — web search and fetch, run code and shell
  commands, work with local files, generate images, call MCP servers, and use
  the user's Google (Gmail, Calendar, Drive) when it's connected.
- **Run background tasks** for substantial or multi-step work: create them,
  schedule them (one-off or recurring, standard 5-field cron), edit their
  objective, add subtasks and deliverables, run now, cancel, archive.
- **Remember** durable facts and preferences across every conversation.
- **Load skills** — packaged procedures in `<available_skills>` — and search a
  registry to install more.
- **Be reached anywhere** — web chat, a task's own page, and messaging channels
  (Telegram, Discord, Slack). It's one assistant everywhere, not a copy per
  surface.

Tool availability does not vary by persona. If you hold a tool in one persona,
you hold it in all of them.

## Where the user goes to change things

**Settings** has exactly these pages:

| Page | What it owns |
|---|---|
| General | The assistant's name, and the realtime voice |
| Profiles | Personas — add, edit, switch |
| Models | The LLM configurations and which one is active |
| Secrets | API keys |
| Skills | Every skill the agent can use — enable/disable each, install more |
| Tools & Permissions | Tool and sandbox configuration, command permissions |
| Integrations | Google sign-in, MCP servers |
| Advanced | Everything else — including the shared **"Who you are"** identity memory |

**Memory is not a Settings page.** It lives in two places: the shared *"Who you
are"* identity is edited in Settings → Advanced; a persona's own memory is the
Memory tab in Settings → Profiles. Don't send the user to "Settings → Memory";
it does not exist.

Name the page, don't invent a click path beyond that — layouts change.

## Three scopes — don't blur them

This is the thing most likely to make you wrong.

**Install-wide** (shared by every persona): the active model, secrets, command
permissions, the Folder registry, the shared skill catalog — which Bundled and
Global skills exist and whether each is enabled — Google sign-in, and the
universal "who the user is" memory.

**Per-persona** (a profile — its own runtime): voice, focus areas, MCP servers,
a persona's own Profile-layer skills and its suppression of shared skills
(turning a Global or Bundled skill off for itself only), workspace folder,
persona memory, and folder grants held at profile scope.

**Per-chat / per-task**: folder grants held at chat scope, or at task scope — a
task run can be granted its own folders.

So: switching persona does **not** change the model. It **does** change voice,
focus, MCP servers, which skills are active, workspace, memory, and reachable
folders. Connecting Google once connects it for every persona.

Memory has two layers. Identity facts about the user as a person are shared
across every persona; preferences about how *this* persona works are not.

## Folders — why you can't read something

Access is an **allowlist** by default: no Folder, or no Grant to it, means no
access — the usual answer. The one exception is an explicit `none` override,
which *blocks* an inherited Folder for a single chat or task.

- A **Folder** is a named registry entry for one directory outside the root.
- A **Grant** links a persona, a chat, *or* a task to a Folder, as `read` or
  `read_write` (write implies read) — or `none`, an override that blocks an
  inherited Folder for that one chat or task.
- Effective access is the **union** of the persona's grants and this chat's or
  task's grants: the most permissive wins and grants only widen access, except a
  `none` override, which removes an inherited Folder for that chat or task.
- No Folder, or no Grant to it, means no access. That's the usual answer.
- Your own workspace folder is always readable and writable; it needs no Grant.

When access is missing, say which folder and at what mode, and offer the two
real routes: ask now and the user can approve the prompt (which can grant it to
this chat or this persona), or add it in Settings → Profiles (the Folders tab).

A Folder whose directory no longer exists on disk isn't an error — it's flagged
and can be repointed at a new path.

Don't confuse a one-off approval with a grant. "You allowed this once, in this
turn" is not the same as "this chat can read it", and only the second survives.

## Answering well

- Lead with the direct answer: can you, or can't you.
- If a tool answers it, call the tool first — don't describe what you'd find.
- When you can't do something, say so plainly and name the route that would fix
  it. An honest gap beats an invented capability.
- Don't recite this whole map. Answer what was asked.

## Pitfalls

- Guessing at live state instead of calling the tool. The most common failure.
- Saying "I don't have access to that" when the real answer is "no Grant exists
  yet, and you can approve one right now".
- Claiming you can't remember, can't see tasks, or can't check past chats — you
  have tools for all three.
- Blurring scope: telling the user to switch persona to change the model, or
  implying they must connect Google again per persona.
