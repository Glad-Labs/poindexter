# Plan setting overrides

A plan the architect composes can change creative settings for itself, or for
one of its steps, without touching `app_settings`. Asking for "Kai calmer
than usual in this one, and Mira in a winter coat" gives that plan a lower
`persona.kai.voice_exaggeration` and a longer `persona.mira.appearance`.
The next plan sees the saved values again.

## In a spec

```json
{
  "name": "vram_cartoon",
  "entry": "cast",
  "setting_overrides": { "persona.kai.voice_exaggeration": 0.5 },
  "nodes": [
    {
      "id": "cast",
      "atom": "story.cast_characters",
      "config": { "setting_overrides": { "story_portrait_attempts": 1 } }
    }
  ]
}
```

A top-level `setting_overrides` applies to every step. One in a node's
`config` applies to that step only, and wins over the top level for the keys
it names (`setting_overrides.merged_overrides`).

## How a step sees them

`build_graph_from_spec` merges the two layers per node, and the atom wrapper
hands the atom `site_config.with_overrides(...)`, a `ScopedSiteConfig`.
That view answers the overridden keys first and sends everything else to the
real instance. That includes `reload()`: casting writes new personas and
reloads so later steps see them, and a copy would swallow that. Read
telemetry also lands on the real instance. Persona reads go through `all()`,
which the view merges, so `persona.<slug>.*` overrides reach `get_persona`.

The view reaches code that reads the step's `site_config`. A service the
container built at startup keeps the real settings.

## What may be overridden

- `architect_setting_overrides_allow` lists the fnmatch patterns a plan may
  touch: `story_*`, `video_cast_*`, `media_house_style`, and three persona
  fields (`voice_exaggeration`, `appearance`, `render_prompt_suffix`).
- `architect_setting_overrides_deny` wins over it: models (cost), URLs,
  directories, path prefixes and anything credential-shaped stay out, so
  `story_writer_model` cannot be switched by a plan.
- Secrets are never in the cache the view reads.

## Checks

At compose time, `check_spec_overrides` sends the architect a FIX for a key
the policy refuses, for one that is not a setting, for a value of the wrong
type (by the key's registered `value_type`), and for an override equal to the
value already in force.

The last one was earned: in live composes the local architect model copied
Kai's 0.75 and Mira's appearance unchanged in two of three runs, which reads
as a change on the plan card and changes nothing. At run time,
`apply_overrides` re-filters through the policy, so a hand-written spec
cannot reach a refused key either.

The same compose step also checks the cast against the intent
(`_named_cast_errors`). A character the intent names must be cast or narrate.
When the intent names any character, the cast holds only named ones (casting
can still design new characters). The architect cast Marta for "Kai and
Mira" in two of three composes before this. A narrator the intent doesn't name is dropped so
casting chooses one: a cyberpunk fairytale got the ten-year-old shepherd
boy as narrator when nobody asked for one.

## Where they show

The architect's prompt lists every overridable key with its current value
(`overridable_settings_block`). The plan card lists each override under
"For this plan only" (`describe_overrides`), and the chat tool's reply names
them so the chat summary mentions them. A step logs
`[setting_overrides] <node>: {...}` when it runs with overrides. A value worth
keeping becomes the saved setting with one `poindexter settings set`.
