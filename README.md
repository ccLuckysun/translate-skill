# translate-skill

`translate-skill` is a Codex skill for translating academic PDF papers while preserving paper-style layout.

`translate-skill` 是一个面向 Codex 的论文翻译 skill，重点是翻译学术 PDF 并尽量保留论文阅读版式。

## Features | 功能特性

- Translate academic PDFs instead of plain text only
- Preserve headings, formulas, figures, citations, and references
- Use the current agent model by default
- Keep legacy external translation services as explicit fallback options only

- 面向论文 PDF，而不是普通纯文本翻译
- 保留标题层级、公式、图表、引用和参考文献结构
- 默认使用当前 agent 背后模型翻译
- 旧外部翻译服务仅作为显式后备方案保留

## Scope | 适用范围

This repository targets the first version of a paper-focused translation skill.  
It is optimized for academic PDFs that need readable translated output with preserved structure.

本仓库当前聚焦第一版论文翻译 skill。  
它主要面向需要“保留结构后再翻译”的学术 PDF。

## Repository Layout | 仓库结构

```text
.
├─ README.md
├─ LICENSE
├─ .gitignore
└─ skill/
   └─ translate-skill/
      ├─ SKILL.md
      ├─ agents/
      │  └─ openai.yaml
      ├─ scripts/
      │  └─ translate_paper.py
      └─ references/
         ├─ workflow.md
         ├─ layout-rules.md
         └─ fallback-services.md
```

## Installation | 安装方式

Copy `skill/translate-skill` into your Codex skills directory, or keep it in a local repository and point your workflow at it.

将 `skill/translate-skill` 复制到你的 Codex skills 目录中，或在本地仓库中维护并按你的工作流加载。

## Usage | 使用方式

Example prompt:

```text
Use $translate-skill to translate this paper PDF while preserving academic layout, formulas, figures, citations, and references.
```

示例提示词：

```text
使用 $translate-skill 翻译这篇论文 PDF，并保留论文排版、公式、图表、引用和参考文献结构。
```

Default executable workflow:

```bash
python skill/translate-skill/scripts/translate_paper.py prepare paper.pdf --out paper.translate-work --source en --target zh
# Use the current Codex agent/model to turn paper.translate-work/segments.json into translations.json.
python skill/translate-skill/scripts/translate_paper.py render paper.translate-work
python skill/translate-skill/scripts/translate_paper.py verify paper.translate-work
```

默认可执行流程：

```bash
python skill/translate-skill/scripts/translate_paper.py prepare paper.pdf --out paper.translate-work --source en --target zh
# 使用当前 Codex agent/模型将 paper.translate-work/segments.json 翻译为 translations.json。
python skill/translate-skill/scripts/translate_paper.py render paper.translate-work
python skill/translate-skill/scripts/translate_paper.py verify paper.translate-work
```

## Default Model Behavior | 默认模型行为

By default, this skill assumes translation is performed by the current agent model.

The user should not need to configure OpenAI, DeepL, Ollama, Azure, or similar services for normal usage.

Legacy external translation services are kept only for explicit compatibility requests.

默认情况下，这个 skill 假定由当前 agent 背后模型直接执行翻译。

正常使用时，用户不应被要求先配置 OpenAI、DeepL、Ollama、Azure 等外部翻译服务。

旧外部翻译服务只作为显式兼容需求保留。

## Differences From Older Plugin Flows | 与旧插件流程的差异

- Agent-first translation instead of API-first translation
- Paper-layout preservation instead of plain translated text output
- Skill-oriented packaging for reuse and GitHub distribution

- 从“先配 API 再翻译”改为“默认 agent 直接翻译”
- 从“输出普通译文”改为“输出保留论文阅读结构的结果”
- 从单插件形态改为可复用、可发布的 skill 结构

## Limitations | 限制与注意事项

- First version is focused on academic PDF translation
- Layout preservation quality still depends on the underlying PDF pipeline
- Extremely complex papers may still need manual review

- 第一版聚焦学术 PDF 翻译
- 保版质量仍取决于底层 PDF 处理链路
- 对极复杂版面的论文，仍可能需要人工复核
