# Layout Preservation Rules

## Preserve First

The translated result should preserve the source paper's structure whenever possible.

Priorities:

1. Formula correctness
2. Reading order
3. Section hierarchy
4. Citation and reference stability
5. Visual similarity

## Sensitive Elements

Treat these as layout-sensitive:

- title
- abstract
- headings
- paragraph boundaries
- inline formulas
- display formulas
- figure captions
- table captions
- citation markers
- reference lists

## Translation Rules

- Translate body text and captions unless the user asks otherwise.
- Preserve formula tokens exactly.
- Preserve all `protected_tokens` from `segments.json` exactly in `translations.json`.
- Preserve numeric anchors and identifiers.
- Preserve bibliography numbering and entry boundaries.
- Keep English bibliography/reference entries unchanged unless the user
  explicitly requests translated references.
- Keep English author names, initials, and Latin-script personal names
  unchanged. Do not transliterate or translate them.
- Keep DOI, URL, journal names, conference names, publisher names, page ranges,
  volume/issue identifiers, and publication metadata unchanged.
- Do not leak internal placeholders into final output.

## Failure Checks

Before considering the task complete, check for:

- broken formulas
- missing captions
- incorrect reference ordering
- duplicated or dropped paragraphs
- obvious layout collapse into plain text
