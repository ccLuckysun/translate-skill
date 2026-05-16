# Agent Translation Interface

The default workflow is two-pass because local Python scripts cannot directly
call the current Codex conversation model.

## Files

`prepare` writes:

- `job.json`: job metadata and expected output paths.
- `segments.json`: extracted text spans for the agent to translate.
- `agent_prompt.md`: task instructions to paste or follow in the agent turn.

The agent writes:

- `translations.json`: translated spans with the same ids and exact source text.

`render` reads `translations.json` and replays translations into pdf2zh so it can
rebuild the mono and dual PDFs.

## Required translations.json shape

```json
{
  "schema_version": "translate-skill.agent.v1",
  "job_id": "copied from job.json",
  "segments": [
    {
      "id": "seg-00000",
      "source": "copy exactly from segments.json",
      "translation": "target-language translation"
    }
  ]
}
```

## Invariants

- Keep segment ids unchanged.
- Copy each `source` exactly.
- Preserve every `protected_tokens` value exactly.
- Copy English bibliography/reference-list entries into `translation`
  unchanged unless the user explicitly requests translated references.
- Keep English author names, initials, and Latin-script personal names
  unchanged; do not transliterate, localize, or translate them.
- Do not merge, split, reorder, omit, or duplicate segments.
