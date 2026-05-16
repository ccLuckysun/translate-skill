# Fallback Translation Services

## Policy

External translation services are compatibility-only options.

They are not the default.

## When To Use

Only use an external service when the user explicitly asks for one, for example:

- "Use DeepL"
- "Run this through OpenAI"
- "Use the old Ollama-based workflow"

## Behavior

When an external service is requested:

- keep the same layout-preservation pipeline
- swap only the text translation backend
- preserve the same placeholder and structure rules

## Migration Guidance

If adapting an older plugin:

- remove assumptions that API keys are mandatory for standard usage
- keep external service configuration behind explicit user choice
- update examples and defaults so they describe agent-first translation
