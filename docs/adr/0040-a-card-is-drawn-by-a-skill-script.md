# A Card is drawn by a Skill script, not by A2UI JSON in the reply

The `rich-views` Skill offers every available **Card** as an in-process script of the same
name. The agent draws one with `run_skill_script(name="rich-views", script="WeatherPanel",
args={…fields})`; the script validates the fields against the Card's schema, builds the A2UI
messages, expands the Card into primitives and publishes them. The model no longer writes the
A2UI envelope (`createSurface` + `updateComponents`, surface and catalog ids, `<a2ui-json>`).

Amends ADRs 0038 and 0039: a Card's detail resource shows the script call, not the messages.

## Context

With the catalog disclosed through a Skill (0038, 0039), a model drew a Card in three steps —
load the Skill, read the Card's detail, then write two A2UI messages as JSON in its reply.
The last step is the fragile one: it is free text parsed after the fact, and a weaker model
gets the envelope, ids or nesting wrong. A tool call is the one structured output every model
is trained for, and a Skill already has a way to offer one: in-process scripts.

Measured on seven prompts through a real gateway turn (six that call for a Card, one control),
two runs each:

| | JSON in the reply | Card as a script |
|---|---|---|
| GPT-5.6 Luna | 14/14 | 14/14, no failed call |
| GPT-5.4 Mini | 2/14 | 3–4/14 |
| First Card of a chat (context) | ~5.6k characters | ~5.9k characters |

Mini's remaining misses are turns where it never loads the Skill — the decision to draw, not
the output format, which this does not change.

## Decision

- **One script per available Card**, named after it, listed in the Skill descriptor; a Card
  that is Disabled or Suppressed has no script, exactly as it has no detail.
- **The body lists the Cards once** (the index) and says every view is a script of the same
  name; it does not repeat each script's schema. The schema and a worked call are in the
  Card's detail resource, read before drawing — the same context cost as before.
- **The script is the validator.** Invalid fields draw nothing and return the errors with the
  Card's schema, so the model fixes the call instead of re-emitting a surface.
- **A2UI JSON in the reply stays supported** — for composing several Cards on one surface or
  building from primitives (`reference/protocol.md`), through the unchanged validation
  middleware.
- **Arguments arrive in more than one shape.** `run_skill_script`'s `args` is untyped
  (`dict | list[str]`), and models send an object, a one-element list holding a JSON object,
  or `--name value` pairs; the script accepts all three until AG2 types script arguments
  (ag2ai/ag2#3327).

## Consequences

- Skill runtimes are tried in turn; a disk runtime rejected an object `args` before checking it
  owned the skill (ag2ai/ag2#3326), so `FilteredSkillRuntime` now answers "not mine" for a
  name it does not hold before delegating.
- A turn with the Skill off has no script to call, as it has no Skill.
- A Card with its own data source (#122) can later take parameters instead of fields through
  the same script.
