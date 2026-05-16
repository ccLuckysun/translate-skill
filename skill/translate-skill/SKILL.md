---
name: translate-skill
description: Use this skill when translating academic PDF papers while preserving the original reading layout, formulas, figures, headings, citations, and references. Prefer it for paper translation tasks that need formatted output instead of plain text translation.
---

# Translate Skill

## Overview

Use this skill for academic paper translation, especially PDF papers with formulas, figures, section headings, citations, and references that must remain readable after translation.

This skill is not a generic text translation utility. Its default goal is to produce a translated paper that still reads like a paper, not a plain text dump.

## When To Use

Use this skill when the user wants any of the following:

- Translate a research paper PDF while preserving layout
- Translate an academic article that contains formulas or figures
- Keep section structure, references, and citation numbering intact
- Produce a translated paper that is still readable in paper form

Do not use this skill for:

- Simple sentence or paragraph translation with no formatting concerns
- General website or UI translation
- Casual document rewriting or summarization

## Workflow

Use the bundled script as a two-pass bridge around pdf2zh/PDFMathTranslate.
The local script does not directly call the current Codex model; the agent is
the translation interface between `prepare` and `render`.

1. Run `scripts/translate_paper.py prepare <paper.pdf> --out <workdir> --source en --target zh`.
2. Read `<workdir>/agent_prompt.md` and `<workdir>/segments.json`.
3. Use the current agent model to write `<workdir>/translations.json` exactly in the requested schema.
4. Run `scripts/translate_paper.py render <workdir>` to replay translations through pdf2zh and generate mono/dual PDFs.
5. Run `scripts/translate_paper.py verify <workdir>` before delivery.

Install the PDF engine when missing:

```bash
python -m pip install "pdf2zh>=1.9,<1.10" pypdf
```

## Translation Behavior

Default translation mode:

- Use the current agent model as the translation engine.
- Treat `segments.json` -> `translations.json` as the agent/model boundary.
- Never claim the Python process can automatically access the current Codex conversation model.
- Do not require the user to configure OpenAI, DeepL, Ollama, Azure, or other external translation APIs.
- Translate in an academic, faithful, terminology-consistent style.
- Return translation only for the requested spans. Do not add explanations or commentary inside translated output.

Compatibility mode:

- Only use legacy external translation services when the user explicitly asks for a specific service.
- Treat old service selection as a fallback path, not the default workflow.

Prompting rules for translated spans:

- Preserve placeholders, formula markers, and citation markers exactly.
- Do not rewrite formulas into prose.
- Do not renumber citations, figures, tables, or references.
- Prefer faithful academic translation over aggressive paraphrase.

## Layout Preservation Rules

The translated output should preserve paper readability as much as possible.

Preserve these elements:

- Title and abstract structure
- Section and subsection hierarchy
- Formula placement and formula contents
- Figure and table anchors, captions, and numbering
- Citation markers and reference numbering
- Paragraph grouping and reading order

Avoid these failures:

- Flattening the paper into plain text
- Breaking formulas or leaking placeholders into final output
- Mixing translated body text into references incorrectly
- Reordering figures, captions, or citation numbers

When a tradeoff is unavoidable, prefer preserving structure and correctness over aggressive reflow.

## Output Expectations

The expected result is a translated paper-style output, not just translated text.

By default, the output should:

- Include both `<stem>-mono.pdf` and `<stem>-dual.pdf`.
- Remain readable as an academic document
- Preserve formulas and structural anchors
- Keep references and citations stable
- Avoid stylistic embellishment outside the source meaning

If the user asks for a specific export format, follow it. Otherwise, prefer the format produced by the existing layout-preserving PDF translation pipeline.

## Resources

Use bundled resources progressively:

- Read `references/workflow.md` for the end-to-end execution model.
- Read `references/agent-interface.md` before creating or validating `translations.json`.
- Read `references/layout-rules.md` when layout preservation details matter.
- Read `references/fallback-services.md` only when the user explicitly requests an external translation service.
- Use `scripts/translate_paper.py` as the local wrapper entrypoint for prepare/render/verify work.
