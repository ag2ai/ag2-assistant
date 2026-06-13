"""Button-based approval gate for shell/code execution.

Closes the `read_file` bypass: shell/code can touch the host filesystem directly,
so without this the agent could route around a denied `read_file` by `cat`-ing
the file. Routed through the *same* turn-level `PermissionManager` as folder
access, so a denial anywhere in the turn stops further command prompts too, and
the user gets the same Allow once / Always allow / Deny buttons.
"""

from autogen.beta.events import ToolCallEvent, ToolResultEvent

from agclaw.permissions import PermissionManager

# Returned to the model on denial — phrased to stop it trying other routes.
_DENIED_RESULT = (
    "The user denied permission. Do not retry or use a different tool/command to "
    "get the same result — tell the user you don't have permission."
)


def require_command_approval():
    """Tool middleware: approve shell/code via the turn-level PermissionManager."""

    async def mw(call_next, event: ToolCallEvent, context):
        manager = context.dependencies.get(PermissionManager)
        if manager is None:
            return ToolResultEvent.from_call(
                event, result="Command blocked: no approver available."
            )
        if await manager.check_command(event.name, event.arguments):
            return await call_next(event, context)
        return ToolResultEvent.from_call(event, result=_DENIED_RESULT)

    return mw
