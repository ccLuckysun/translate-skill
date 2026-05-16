# Translate Skill Workflow

## Purpose

This skill is for academic PDF translation with layout preservation. The output should still behave like a readable paper after translation.

## Default Execution Model

1. Run `scripts/translate_paper.py prepare <paper.pdf> --out <workdir>`.
2. The script uses pdf2zh/PDFMathTranslate to expose paragraph-level text spans and writes `segments.json`.
3. The agent reads `agent_prompt.md` and translates `segments.json` into `translations.json`.
4. Run `scripts/translate_paper.py render <workdir>` to replay translations through pdf2zh.
5. Run `scripts/translate_paper.py verify <workdir>` and inspect the mono/dual PDFs before delivery.

The Python script cannot directly call the current Codex conversation model.
The model boundary is the `segments.json` -> `translations.json` handoff.

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
