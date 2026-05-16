# Agent Translation Interface

The default workflow is two-pass because local Python scripts cannot directly
call the current Codex conversation model.

## Files

`prepare` writes:

- `job.json`: job metadata and expected output paths.
- `segments.json`: extracted text spans for the agent to translate.
- `segments-preview.md`: UTF-8 human-readable source preview with protected tokens and segment types.
- `agent_prompt.md`: task instructions to paste or follow in the agent turn.

The agent writes:

- `translations.json`: translated spans with the same ids and exact source text.

Run `validate` before `render`. It writes `validate-report.json` and, when possible, `translation-preview.md`. For Chinese targets, the report includes translation quality metrics and validation blocks obvious encoding damage such as all-`?` translations or body translations with no CJK text.

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
- Keep protected labels such as `Eq. 6`, `Figure 1`, and `Table 2` unchanged; do not translate them into localized labels.
- Copy English bibliography/reference-list entries into `translation`
  unchanged unless the user explicitly requests translated references.
- Keep English author names, initials, and Latin-script personal names
  unchanged; do not transliterate, localize, or translate them.
- Do not merge, split, reorder, omit, or duplicate segments.

## Review Notes

Do not decide that extracted text is corrupted from PowerShell `Get-Content` alone. Unicode ligatures such as `ﬁ` and `ﬂ` may display as garbled characters in the terminal while the JSON file is still valid UTF-8. Prefer `segments-preview.md`, `translation-preview.md`, or a UTF-8-aware reader for inspection.

On Windows PowerShell, do not create `translations.json` by piping a large Chinese here-string into `python -`. Write Chinese text into a UTF-8 file, or have Python read an existing UTF-8 JSON/Markdown source, so the shell does not replace characters with `?` before Python sees them.

If a command logs `KeyError: 'post'` from font subsetting but exits successfully, treat it as a warning rather than an automatic failure. Still run `validate`, `render`, `verify`, and inspect the produced PDFs.
