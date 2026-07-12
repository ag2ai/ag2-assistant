"""Orchestrate one coding run against a host CLI agent.

Flow: resolve the agent → gate the working directory via the assistant's
``PermissionManager`` → snapshot the tree → run the coding turn (streaming its
live events onto the chat via the shared stream) → compute the diff → emit a
durable CodingSession surface and return a concise summary for the main agent.

The real ACP run is behind the ``runner`` seam so it can be exercised
deterministically in tests; the default runner drives ``ag2.acp``.
"""

from uuid import uuid4

from ag2.acp.events import ACPPlan

from assistant.coding import config as cfgmod
from assistant.coding import detect, diff
from assistant.coding.surface import build_surface

_NO_AGENT = (
    "No coding agent available on this host. Install one whose ACP adapter is on "
    "PATH: claude-agent-acp (Claude Code), codex-acp (Codex), or opencode (OpenCode)."
)


async def _default_runner(config, task, context, asker=None):
    """Drive the CLI agent over ACP as a one-shot sub-run on the caller's stream.

    Passing ``stream=context.stream`` surfaces the agent's live ACP events
    (messages, thoughts, tool calls, plan) onto the chat. ``permission_policy``
    on the config routes ``session/request_permission`` to ``context.input`` via
    the hitl hook, so file/command approvals reach the user.
    """
    from ag2 import Agent

    hitl_hook = None
    if asker is not None:
        from assistant.hitl import build_hitl_hook

        hitl_hook = build_hitl_hook(asker)

    coder = Agent("coding-agent", config=config)
    try:
        reply = await coder.ask(task, stream=context.stream, config=config, hitl_hook=hitl_hook)
        return getattr(reply, "body", "") or ""
    finally:
        await config.aclose()


def _plan_from_event(event: ACPPlan) -> list[dict]:
    return [{"content": e.content, "status": e.status} for e in event.entries]


def _summary(label: str, directory: str, files: list, reply: str) -> str:
    if files:
        names = ", ".join(f.path for f in files[:8])
        more = f" (+{len(files) - 8} more)" if len(files) > 8 else ""
        head = f"{label} finished in {directory}. {len(files)} file(s) changed: {names}{more}."
    else:
        head = f"{label} finished in {directory}. No file changes were detected."
    reply = (reply or "").strip()
    return f"{head}\n\n{reply}" if reply else head


async def run_coding_session(
    *,
    context,
    directory: str,
    task: str,
    agent: str = "",
    pm=None,
    asker=None,
    surface_id: str | None = None,
    runner=None,
) -> str:
    """Run a coding task with a host CLI agent; return a summary for the agent.

    When ``AG2ASSISTANT_ACP_BRIDGE`` is set (e.g. running in Docker), agents are
    discovered and driven through the host bridge; otherwise the agent is spawned
    as a local subprocess. Detection and diffing are identical in both modes.
    """
    endpoint = detect.bridge_endpoint()
    if endpoint is not None:
        from assistant.coding import bridge_client

        try:
            inventory = await bridge_client.list_agents(endpoint)
        except Exception as exc:  # noqa: BLE001 — unreachable/refused bridge, don't crash the turn
            return (
                f"Couldn't reach the host coding bridge at {endpoint.host}:{endpoint.port} "
                f"({exc}). Is `ag2-assistant acp-bridge` running on the host?"
            )
        info = detect.pick(inventory, agent)
        if info is None:
            avail = [a.label for a in inventory if a.available]
            detail = f" Available via the host bridge: {', '.join(avail)}." if avail else ""
            return _NO_AGENT + detail
    else:
        info = detect.resolve_agent(agent)
        if info is None:
            avail = [a.label for a in detect.available_agents()]
            detail = f" Available: {', '.join(avail)}." if avail else ""
            return _NO_AGENT + detail

    if pm is None:
        return "I can't run a coding agent without a permission authority to approve the folder."
    if not await pm.check(directory):
        return (
            f"I don't have permission to write in {directory}, so I can't run the "
            f"{info.label} coding agent there. Approve the folder and ask again."
        )

    sid = surface_id or f"coding-{uuid4().hex[:8]}"
    config = cfgmod.build_config(info, directory, endpoint=endpoint)
    baseline = diff.capture(directory)

    # running surface
    await context.send(
        build_surface(
            surface_id=sid,
            agent_label=info.label,
            directory=directory,
            task=task,
            status="running",
            files=[],
        )
    )

    # capture the agent's plan updates as they stream on the shared stream
    latest_plan: list[dict] = []

    async def _watch(event):  # positional
        if isinstance(event, ACPPlan):
            latest_plan[:] = _plan_from_event(event)

    sub = context.stream.subscribe(_watch)
    run = runner or (lambda c, t, ctx: _default_runner(c, t, ctx, asker=asker))
    try:
        try:
            reply = await run(config, task, context)
        finally:
            context.stream.unsubscribe(sub)
    except Exception as exc:  # noqa: BLE001 — surface any adapter failure, don't crash the turn
        files = diff.compute_diff(baseline, directory)
        await context.send(
            build_surface(
                surface_id=sid,
                agent_label=info.label,
                directory=directory,
                task=task,
                status="failed",
                files=files,
                plan=latest_plan,
                error=str(exc),
            )
        )
        return f"The {info.label} coding run failed: {exc}"

    files = diff.compute_diff(baseline, directory)
    summary = _summary(info.label, directory, files, reply)
    await context.send(
        build_surface(
            surface_id=sid,
            agent_label=info.label,
            directory=directory,
            task=task,
            status="done",
            files=files,
            plan=latest_plan,
            summary=summary,
        )
    )
    return summary
