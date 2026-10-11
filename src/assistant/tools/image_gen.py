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
from typing import Annotated
from urllib.parse import quote

from ag2 import Agent, Context, tool
from ag2.config import OpenAIResponsesConfig
from ag2.config.gemini import GeminiConfig
from ag2.tools import ImageGenerationTool
from pydantic import Field

from assistant.attachments import build_input
from assistant.codex_auth import BACKEND_BASE, CodexAuth, default_headers
from assistant.config import Config
from assistant.events import ImageGenerated
from assistant.llm_configs import LlmConfigStore, image_capable
from assistant.secrets import KEY_ENV
from assistant.workspace import resolve, write_image

# Default image models (overridable via env so they track provider deprecations).
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-3.1-flash-lite-image"


def _image_config(config: Config) -> Config | None:
    """Resolve an image-capable configuration with credentials, using only local state."""
    if not config.llm.config_type and not config.llm.env_pinned:
        store = LlmConfigStore(config.paths)
        if store.list_configs():
            entry = store.image_entry()
            if entry is None:
                return None
            config = config.model_copy(deep=True)
            store.derive_onto(config, entry)
    provider = (config.llm.provider or "gemini").lower()
    provider = "gemini" if provider == "google" else provider
    ctype = config.llm.config_type or provider
    if provider == "openai" and config.llm.auth_mode == "subscription":
        ctype = "openai_subscription"
    options = config.llm.provider_options.get(provider, {})
    if not image_capable({"type": ctype, "base_url": options.get("base_url")}):
        return None
    if provider == "openai" and options.get("base_url"):
        return None
    if ctype == "openai_subscription":
        return config if CodexAuth(config.paths).status()["signed_in"] else None
    key = options.get("api_key") or config.secret_env.get(KEY_ENV.get(provider, ""), "")
    return config if key else None


def image_available(config: Config) -> bool:
    """Whether the selected model can generate images with its configured credentials."""
    return _image_config(config) is not None


def _image_agent(config: Config):
    """A one-shot image-capable Agent, or None when the selected configuration cannot run."""
    selected = _image_config(config)
    if selected is None:
        return None
    config = selected
    provider = (config.llm.provider or "gemini").lower()
    provider = "gemini" if provider == "google" else provider
    model = config.llm.model
    options = config.llm.provider_options.get(provider, {})
    key = options.get("api_key") or config.secret_env.get(KEY_ENV.get(provider, ""), "")

    if config.llm.auth_mode == "subscription" and provider == "openai":
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
        image_model = config.image_model or DEFAULT_GEMINI_IMAGE_MODEL
        gemini_cfg = GeminiConfig(
            model=image_model, api_key=key, response_modalities=["TEXT", "IMAGE"]
        )
        return Agent("imager", config=gemini_cfg)
    if provider == "openai":
        cfg = OpenAIResponsesConfig(model=model, api_key=key)
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


async def _first_image(reply) -> tuple[bytes, str] | None:
    """Extract the first generated image (bytes, media_type) from a reply, or None."""
    for f in getattr(reply, "files", None) or []:
        getter = getattr(f, "content", None)
        data = await getter() if callable(getter) else (getattr(f, "data", b"") or b"")
        if data:
            media = (getattr(f, "metadata", {}) or {}).get("media_type") or "image/png"
            return data, media
    return None


def build_image_tool(config, workspace_dir):
    """A `generate_image` tool bound to the active provider + the workspace."""

    @tool
    async def generate_image(
        prompt: Annotated[str, Field(description="A full description of the image to create.")],
        context: Context,
        source_image: Annotated[
            str | None,
            Field(
                description="Optional workspace path of an existing image to EDIT instead "
                "of generating from scratch — pass the path a previous generate_image "
                "returned to modify that image (e.g. 'change the sky to sunset')."
            ),
        ] = None,
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

        found = await _first_image(reply)
        if found is None:
            return "No image was produced — try rephrasing the description."
        data, media = found
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
        url = _workspace_file_url(config, rel)
        served = f" It is served at {url}." if url else ""
        return (
            f"Generated image saved to {rel}. {shown} To modify it, call this tool "
            f"again with source_image='{rel}'.{served}"
        )

    return generate_image
