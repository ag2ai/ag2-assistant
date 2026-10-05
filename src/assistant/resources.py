"""Profile-owned resource generations held by invocations until their scopes exit."""

import asyncio
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from assistant.observability import log_suppressed


def fingerprint(value: Any) -> str:
    """Identify effective settings without exposing their credential values."""
    encoded = json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def _finish_cleanup(awaitable: Awaitable) -> None:
    """Complete owned cleanup before propagating cancellation, including repeated cancellation."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            cancelled = True
            if task.done():
                break
    task.result()
    if cancelled:
        raise asyncio.CancelledError


@dataclass(eq=False)
class _Generation:
    kind: str
    key: str
    value: Any
    holders: int = 0
    retired: bool = False
    closed: bool = False
    closing: asyncio.Task | None = None

    async def dispose(self) -> None:
        if self.closed or self.holders or not self.retired:
            return
        if self.closing is None:
            self.closing = asyncio.create_task(self._close())
        await _finish_cleanup(self.closing)

    async def _close(self) -> None:
        close = getattr(self.value, "aclose", None) or getattr(self.value, "close", None)
        if close is not None:
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                log_suppressed(f"closing {self.kind}", exc)
        self.closed = True


class ResourceLease:
    """A scope retaining each acquired generation until success, failure or cancellation."""

    def __init__(self, owner: "ProfileResources") -> None:
        self._owner = owner
        self._held: list[_Generation] = []

    def acquire(self, kind: str, key: str, factory: Callable) -> Any:
        generation = self._owner.acquire(kind, key, factory)
        if generation not in self._held:
            generation.holders += 1
            self._held.append(generation)
        return generation.value

    async def acquire_async(self, kind: str, key: str, factory: Callable[..., Awaitable]) -> Any:
        generation = await self._owner.acquire_async(kind, key, factory)
        if generation not in self._held:
            generation.holders += 1
            self._held.append(generation)
        return generation.value

    async def __aenter__(self) -> "ResourceLease":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        held, self._held = self._held, []
        for generation in held:
            generation.holders -= 1
        await _finish_cleanup(self._dispose(held))

    async def _dispose(self, held: list[_Generation]) -> None:
        for generation in reversed(held):
            await generation.dispose()
        self._owner._retired.difference_update(
            generation for generation in held if generation.closed
        )


class ProfileResources:
    """Reusable resources for one Profile; retirement stops new acquisitions immediately."""

    def __init__(self) -> None:
        self._current: dict[tuple[str, str], _Generation] = {}
        self._closed = False
        self._retired: set[_Generation] = set()

    def lease(self) -> ResourceLease:
        return ResourceLease(self)

    def acquire(self, kind: str, key: str, factory: Callable) -> _Generation:
        if self._closed:
            raise RuntimeError("Profile resources closed")
        slot = (kind, key)
        generation = self._current.get(slot)
        if generation is None:
            generation = _Generation(kind, key, factory())
            self._current[slot] = generation
        return generation

    async def acquire_async(
        self, kind: str, key: str, factory: Callable[..., Awaitable]
    ) -> _Generation:
        if self._closed:
            raise RuntimeError("Profile resources closed")
        slot = (kind, key)
        generation = self._current.get(slot)
        if generation is None:
            generation = _Generation(kind, key, await factory())
            self._current[slot] = generation
        return generation

    async def reconcile(self, kind: str, keys: Iterable[str]) -> None:
        wanted = set(keys)
        retired = []
        for slot, generation in list(self._current.items()):
            if generation.kind == kind and generation.key not in wanted:
                self._current.pop(slot)
                generation.retired = True
                retired.append(generation)
        self._retired.update(retired)
        try:
            await _finish_cleanup(self._dispose(retired))
        finally:
            self._retired.difference_update(
                generation for generation in retired if generation.closed
            )

    async def _dispose(self, generations: Iterable[_Generation]) -> None:
        for generation in generations:
            await generation.dispose()

    async def aclose(self) -> None:
        self._closed = True
        generations = list(self._current.values()) + list(self._retired)
        self._current.clear()
        for generation in generations:
            generation.retired = True
        try:
            await _finish_cleanup(self._dispose(generations))
        finally:
            self._retired.difference_update(
                generation for generation in generations if generation.closed
            )


class SharedEnvironment:
    """A sandbox factory whose owned environment survives AG2 constructor tool copies."""

    def __init__(self, environment) -> None:
        self._environment = environment

    def __deepcopy__(self, memo):
        return self

    def open(self, context=None):
        return self._environment.open(context)

    @property
    def workdir(self):
        return getattr(self._environment, "workdir", PurePosixPath("/workspace"))

    async def aclose(self) -> None:
        close = getattr(self._environment, "aclose", None)
        if close is not None:
            await close()
