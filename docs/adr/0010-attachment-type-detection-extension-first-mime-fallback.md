# Attachment type detection is extension-first, with a MIME fallback

`build_input` turns a chat attachment's bytes into the right AG2 `Input` —
`ImageInput` for pictures (so the model sees them via vision), `AudioInput` /
`VideoInput` / `DocumentInput` for media and PDFs, `TextInput` for inlined text.
It has always chosen that type from the **filename's extension**. The platform
`media_type` (MIME) was only ever a value passed *into* the built Input, never
used to route it.

That was fine while every attachment path carried a real filename (channel
downloads, the file picker). It breaks the moment a file arrives without one:
pasting a screenshot yields bytes with an empty/extensionless name and a real
`image/png` MIME. Extension detection misses, and the image fell through to the
generic `DocumentInput` branch — the model got a binary document instead of a
picture, defeating the whole point of pasting an image.

We keep extension as the **primary** key and add MIME as a **fallback**: when the
extension resolves nothing, route by `media_type` (`image/*` → `ImageInput`,
`audio/*`, `video/*`, `application/pdf` → `DocumentInput`, `text/*` → inlined
`TextInput`), and only then fall through to the generic document.

## Considered options

- **MIME-first detection** — route by `media_type`, fall back to extension. More
  "correct" in the abstract, but platform MIME types are unreliable (channels
  send `application/octet-stream` for known-good files; browsers vary), whereas an
  extension the user chose is a strong, stable signal. Flipping the priority would
  regress the common, working case to fix the rare one.
- **Fix only the frontend** (synthesise a good filename before send, leave
  `build_input` as-is) — closes the paste hole but leaves the backend fragile for
  every other caller. Telegram/Discord/Slack that forward a MIME without a usable
  extension would still misclassify. We do the frontend synthesis *as well*, but
  the backend fallback is what makes the detection correct for all callers.

## Consequences

- **Extension still wins.** A file named `pic.png` is an image even if the MIME
  claims `application/octet-stream`. The fallback only runs when the extension is
  unknown or absent, so no existing behaviour changes.
- **Pasted/dropped nameless files route correctly.** A clipboard screenshot
  (`image/png`, no name) now becomes an `ImageInput`. The frontend still
  synthesises a `pasted-N.png` name (so the extension path usually catches it),
  but the backend fallback is the guarantee, independent of the caller.
- **The fix is install-wide.** Every channel that reaches `build_input` with a
  MIME but no extension benefits, not just the web composer.
- **Unknown binary is unchanged.** No extension and a non-media MIME still lands
  on `DocumentInput(application/octet-stream)` — the same last resort as before,
  now reached deliberately rather than by accident.
