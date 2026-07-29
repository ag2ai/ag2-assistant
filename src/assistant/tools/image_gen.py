"""Image generation + editing as a single tool, provider-aware.

The universal chat agent composes the `prompt` from the conversation and calls this
tool; the tool runs a one-shot image-capable model call, saves the result into the
workspace (so it shows in the Files browser / image preview), and returns the path.
For edits, pass `source_image` (a workspace path from a prior call) — the existing
image is sent back to the model so it *edits* rather than regenerates.

- Gemini: native — ``GeminiConfig(model=<image model>, response_modalities=["TEXT","IMAGE"])``;
  the generated/edited image arrives on ``reply.files``.
- OpenAI: ``OpenAIResponsesConfig`` + the native ``ImageGenerationTool``; same surface.
Other providers (Anthropic/Ollama) have no image model → the tool says so.
"""

import contextlib
import os
from typing import Annotated
from urllib.parse import quote

from ag2 import Agent, Context, tool
from ag2.config import OpenAIResponsesConfig
from ag2.config.gemini import GeminiConfig
from ag2.tools import ImageGenerationTool
from pydantic import Field

from assistant.attachments import build_input
from assistant.codex_auth import BACKEND_BASE, CodexAuth, default_headers
from assistant.events import ImageGenerated
from assistant.llm_configs import PROVIDER_OF, LlmConfigStore
from assistant.secrets import KEY_ENV, SecretStore
from assistant.workspace import resolve, write_image

# Default image models (overridable via env so they track provider deprecations).
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-lite-image"


def _image_agent(config):
    """A one-shot image-capable Agent, or None if none is available.

    Images follow the SELECTED configuration: the active entry runs them iff its type
    is image-capable (``llm_configs.image_entry()``), with its referenced Secret
    (falling back to the provider's env key). No fallback hunting through the list —
    switching the active model never silently routes images through another config.
    When the store is empty, falls back to the flat ``config.llm.provider`` with the
    provider's env key. Built directly (not via model_config) to sidestep an import
    cycle."""
    store = LlmConfigStore(config.paths)
    entry = None
    if store.list_configs():
        entry = store.image_entry()
        if entry is None:
            return None  # the ACTIVE config can't generate images
        provider = PROVIDER_OF[entry["type"]]
        model = entry["model"]
    else:
        provider = (config.llm.provider or "gemini").lower()
        if provider in ("google", ""):
            provider = "gemini"
        model = config.llm.model

    def _key(name: str) -> str:
        # Prefer the entry's referenced Secret; fall back to the provider key the
        # resolved config carries (a Default Secret contributes it via secret_env).
        own = SecretStore(config.paths).secret_value(entry.get("secret_id", "")) if entry else ""
        return own or config.secret_env.get(KEY_ENV.get(name, ""), "")

    if entry is not None and entry["type"] == "openai_subscription":
        # ChatGPT subscription: the backend runs the native image tool too (verified
        # live — AG2 captures the streamed image on reply.files). Same construction
        # rules as model_config's subscription branch: token as bearer, Codex headers,
        # streaming forced on, storage forced off.
        creds = CodexAuth(config.paths).creds_best_effort()
        cfg = OpenAIResponsesConfig(
            model=model,
            api_key=creds.access_token,
            base_url=BACKEND_BASE,
            default_headers=default_headers(creds),
            streaming=True,
            store=False,
        )
        return Agent("imager", config=cfg, tools=[ImageGenerationTool()])

    if provider == "gemini":
        image_model = os.environ.get("AG2ASSISTANT_IMAGE_MODEL") or DEFAULT_GEMINI_IMAGE_MODEL
        cfg = GeminiConfig(
            model=image_model, api_key=_key("gemini"), response_modalities=["TEXT", "IMAGE"]
        )
        return Agent("imager", config=cfg)
    if provider == "openai":
        cfg = OpenAIResponsesConfig(model=model, api_key=_key("openai"))
        return Agent("imager", config=cfg, tools=[ImageGenerationTool()])
    return None  # anthropic / ollama: no image generation


def _workspace_file_url(config, path: str) -> str:
    """Return the profile-scoped browser URL for a workspace file."""
    profiles_dir = config.root_dir / "profiles"
    try:
        relative_data_dir = config.data_dir.resolve().relative_to(profiles_dir.resolve())
    except ValueError:
        return ""
    if not relative_data_dir.parts:
        return ""
    profile_id = relative_data_dir.parts[0]
    return f"/api/p/{quote(profile_id, safe='')}/files/raw?path={quote(path, safe='')}"


async def _first_image(reply) -> tuple[bytes, str] | tuple[None, None]:
    """Extract the first generated image (bytes, media_type) from a reply, or (None, None)."""
    for f in getattr(reply, "files", None) or []:
        getter = getattr(f, "content", None)
        data = await getter() if callable(getter) else (getattr(f, "data", b"") or b"")
        if data:
            media = (getattr(f, "metadata", {}) or {}).get("media_type") or "image/png"
            return data, media
    return None, None


def build_image_tool(config, workspace_dir):
    """A `generate_image` tool bound to the active provider + the workspace."""

    @tool
    async def generate_image(
        prompt: Annotated[str, Field(description="A full description of the image to create.")],
        source_image: Annotated[
            str | None,
            Field(
                description="Optional workspace path of an existing image to EDIT instead "
                "of generating from scratch — pass the path a previous generate_image "
                "returned to modify that image (e.g. 'change the sky to sunset')."
            ),
        ] = None,
        context: Context = None,
    ) -> str:
        """Generate an image from a description, or edit one you already made. Saves the
        image into the workspace and returns its path. To modify an image, call again
        with source_image set to that image's path."""
        agent = _image_agent(config)
        if agent is None:
            return (
                "Image generation isn't available on the selected model configuration. "
                "Switch the active configuration in Settings → Models to one that can "
                "generate images (Gemini, OpenAI, or the ChatGPT subscription)."
            )

        parts: list = [prompt]
        if source_image:
            p = resolve(workspace_dir, source_image)
            if p is None:
                return f"Couldn't find the source image '{source_image}' in the workspace."
            parts.append(build_input(p.read_bytes(), p.name))

        try:
            reply = await agent.ask(*parts)
        except Exception as exc:
            return f"Image generation failed: {exc}"

        data, media = await _first_image(reply)
        if not data:
            return "No image was produced — try rephrasing the description."
        rel = write_image(workspace_dir, prompt, data, media)
        # Emit onto the stream so the client renders an inline thumbnail (the path
        # lives in the result, not the call args, so a card alone can't show it).
        emitted = False
        if context is not None:
            with contextlib.suppress(Exception):
                await context.send(ImageGenerated(rel, prompt=prompt, media_type=media))
                emitted = True
        shown = (
            "It has been shown to the user inline already."
            if emitted
            else "It has NOT been shown to the user — present it however this surface allows."
        )
        canvas_url = _workspace_file_url(config, rel)
        canvas_instruction = (
            f" To show it in an A2UI Image component, use this exact url value: {canvas_url}."
            if canvas_url
            else ""
        )
        return (
            f"Generated image saved to {rel}. {shown} To modify it, call this tool "
            f"again with source_image='{rel}'.{canvas_instruction}"
        )

    return generate_image
