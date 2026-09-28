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

## Amendment (ADR 0030, the type gate)

Two of the values this record's chain hands to an AG2 constructor are ones AG2 does
not declare and no supported provider accepts. Both were found by running the type
checker over `attachments.py` for the first time, and neither raises locally: AG2
performs no runtime validation, so the value travels to the provider, which rejects
it, and the user sees a failed turn with no local reproduction.

- **`.m4a` → `audio/mp4`.** AG2's audio literal is wav/mpeg/ogg/flac/aiff/aac. The
  Anthropic message API has no audio input modality at all, the OpenAI chat
  completion interface accepts only WAV and MP3, and the Gemini audio list omits it.
  The entry is **removed from the extension table**, so an `.m4a` resolves no kind and
  takes this record's own fallback chain.
- **`application/octet-stream`, the last resort above.** AG2's document literal has no
  generic-binary member; Anthropic's document block declares its base64 source's media
  type as the constant `application/pdf` (its only other source is `text/plain`),
  Gemini's document processing accepts PDF plus text-shaped types, and OpenAI's file
  part carries no media type at all. So **every** unknown binary attachment already
  failed at the provider. Widening AG2's literal was rejected as the fix: it would make
  the value type-check while leaving it rejected on the wire.

Two narrowings to what is written above:

1. **The extension now decides the media type as well as the kind.** This record says
   "extension still wins", and it did decide the *kind* — but the value passed in was
   `media_type or <our table>`, so a platform MIME outranked our own table for a file
   whose extension we recognised. A `pic.png` sent with `application/octet-stream` was
   built as an `ImageInput` carrying `application/octet-stream`. The platform value is
   now discarded when the extension resolves a kind, which is what this record's own
   reasoning about unreliable MIME types already implied.
2. **A platform-supplied MIME is validated before it is forwarded.** The fallback still
   routes by `media_type`, but only to a constructor that declares that value.

**"Unknown binary is unchanged" no longer holds** — that consequence is superseded.
An attachment we cannot type honestly becomes a short `TextInput` note naming the file
and its media type, so the agent knows a file arrived and can ask about it or reach for
a tool. Two user-visible changes follow: an `.m4a` voice note reaches the agent as a
note rather than as audio, and an unknown binary reaches it as a note rather than as a
document. Both are intentional — each previously reached the agent in a form the
provider rejected.

HEIC/HEIF stay out: AG2's image literal omits both, Gemini documents them as supported,
and widening that literal is an upstream change (tracked as ag2ai/ag2#3147). Adding a
HEIC entry to our table before it lands would only produce a type error the gate blocks.
Under the note above, a nameless HEIC paste now arrives as a note rather than as a
rejected image — an improvement, not a substitute for the upstream fix.
