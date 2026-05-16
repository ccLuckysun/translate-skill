# Translate Skill Workflow

## Purpose

This skill is for academic PDF translation with layout preservation. The output should still behave like a readable paper after translation.

## Default Execution Model

1. Run `scripts/translate_paper.py prepare <paper.pdf> --out <workdir>`.
2. The script uses pdf2zh/PDFMathTranslate to expose paragraph-level text spans and writes `segments.json`, `segments-preview.md`, and `agent_prompt.md`.
3. The agent reads `agent_prompt.md` and translates `segments.json` into `translations.json`.
4. Run `scripts/translate_paper.py validate <workdir>` to catch schema, ordering, source, protected-token, reference-entry, and obvious translation content errors before rendering.
5. Run `scripts/translate_paper.py render <workdir>` to replay translations through pdf2zh and write `translation-preview.md`.
6. Run `scripts/translate_paper.py verify <workdir>` and inspect the mono/dual PDFs before delivery.

The Python script cannot directly call the current Codex conversation model.
The model boundary is the `segments.json` -> `translations.json` handoff.

## Validation and Review

`validate` is mandatory before `render`. Fix every reported error, especially missing `protected_tokens` such as `Eq. 6`, `Figure 1`, or `Table 2`.

For Chinese targets, validation also blocks likely encoding damage: non-reference translations with no CJK characters and translations with abnormal `?` counts. These failures usually mean `translations.json` was damaged before render, often by an unsafe shell or pipe encoding path.

Use the markdown previews for human checks:

- `segments-preview.md`: source segments, detected segment type, and protected tokens.
- `translation-preview.md`: source/translation pairs plus issues such as missing tokens, empty translations, translated reference entries, or visibly damaged target text.

If PowerShell displays strange characters in `segments.json`, check the preview file or another UTF-8 reader before assuming extraction failed. Unicode ligatures such as `ﬁ` and `ﬂ` can render poorly in terminal output.

On Windows PowerShell, do not pipe large Chinese here-strings into Python. Save Chinese text as UTF-8 files and have Python read those files, otherwise characters can become `?` before JSON is written.

Some PDFs may emit a font subsetting warning containing `KeyError: 'post'`. Treat it as non-blocking only when the command exits successfully and `validate`/`verify` pass.

## Default Translator Policy

Use the current agent model by default.

Do not make external API configuration a prerequisite for normal use.

Only switch to legacy external translators when the user explicitly requests one.

## Reuse Guidance

When adapting an existing PDF translation plugin or repository:

- Keep layout extraction and reconstruction logic when it is already proven.
- Replace "user must configure translation API" assumptions with "agent-first translation" behavior.
- Keep compatibility paths minimal and explicit.
- Avoid turning the skill into a generic translation toolbox.

## Expected Deliverable Shape

Prefer outputs that preserve:

- mono PDF translated into the target language
- dual PDF with original and translated pages
- paper reading order
- section hierarchy
- formulas
- figure and table anchors
- references and citation numbering

If a task requests code changes, implementation should separate:

- translation logic
- placeholder and structure protection
- layout reconstruction
