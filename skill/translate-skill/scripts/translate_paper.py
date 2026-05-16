#!/usr/bin/env python3
"""
Two-pass agent translation bridge for layout-preserving paper translation.

pdf2zh/PDFMathTranslate owns PDF parsing, formula protection, layout rebuild,
and mono/dual PDF export. The Codex agent owns the actual translation by
turning segments.json into translations.json between prepare and render.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import json
import re
import sys
import types
import uuid
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "translate-skill.agent.v1"
DEFAULT_SOURCE_LANGUAGE = "en"
DEFAULT_TARGET_LANGUAGE = "zh"
PDF2ZH_VERSION_PREFIX = "1.9."
SEGMENT_BODY = "body"
SEGMENT_REFERENCE_HEADING = "reference_heading"
SEGMENT_REFERENCE_ENTRY = "reference_entry"
SEGMENT_TYPES = {SEGMENT_BODY, SEGMENT_REFERENCE_HEADING, SEGMENT_REFERENCE_ENTRY}
QUESTION_MARK_TOTAL_LIMIT = 100
QUESTION_MARK_SEGMENT_LIMIT = 20
QUESTION_MARK_SEGMENT_RATIO = 0.3

_TOKEN_RE = re.compile(
    r"(\{v\d+\}|<b\d+>|</b\d+>|\{\{v\d+\}\}|\[[0-9,\-\s]+\]|"
    r"\([A-Za-z][A-Za-z0-9_.-]*,\s*\d{4}[a-z]?\)|"
    r"\b(?:Fig|Figure|Table|Eq|Equation|Sec|Section)\.?\s*\d+(?:\.\d+)*)"
)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


class SkillError(RuntimeError):
    """Expected user-facing failure."""


@dataclass(frozen=True)
class Segment:
    id: str
    index: int
    source: str
    source_hash: str
    protected_tokens: list[str]
    segment_type: str = SEGMENT_BODY


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError as exc:
        raise SkillError(f"Missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SkillError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillError(f"{path} must contain a JSON object.")
    return data


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


def ensure_real_pdf(path: Path) -> None:
    if not path.exists():
        raise SkillError(f"Input PDF does not exist: {path}")
    if path.suffix.lower() != ".pdf":
        raise SkillError("Input must be a .pdf file.")
    try:
        with path.open("rb") as file:
            header = file.read(5)
    except OSError as exc:
        raise SkillError(f"Cannot read input PDF: {path}") from exc
    if header != b"%PDF-":
        raise SkillError(
            "Input is not a valid PDF header. Provide a real PDF, not a renamed file."
        )

    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        # pdf2zh depends on PDF tooling at render time. Header validation keeps
        # prepare errors friendly when pypdf is not installed yet.
        return

    try:
        reader = PdfReader(str(path))
        if len(reader.pages) == 0:
            raise SkillError("Input PDF has no readable pages.")
    except SkillError:
        raise
    except Exception as exc:
        raise SkillError(f"Input PDF cannot be parsed: {exc}") from exc


def ensure_pdf2zh_available() -> types.ModuleType:
    try:
        pdf2zh = importlib.import_module("pdf2zh")
        importlib.import_module("pdf2zh.high_level")
        importlib.import_module("pdf2zh.converter")
        importlib.import_module("pdf2zh.translator")
    except ImportError as exc:
        raise SkillError(
            "pdf2zh/PDFMathTranslate is required for prepare/render. "
            "Install it with: python -m pip install 'pdf2zh>=1.9,<1.10' "
            "or pin python -m pip install 'pdf2zh==1.9.11' if dependency "
            "resolution hangs."
        ) from exc

    version = getattr(pdf2zh, "__version__", "")
    if not str(version).startswith(PDF2ZH_VERSION_PREFIX):
        raise SkillError(
            f"Unsupported pdf2zh version {version!r}. "
            "This skill targets pdf2zh 1.9.x; install with: "
            "python -m pip install 'pdf2zh>=1.9,<1.10' "
            "or pin python -m pip install 'pdf2zh==1.9.11' if dependency "
            "resolution hangs."
        )
    return pdf2zh


def extract_protected_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text):
        token = match.group(0)
        if token not in tokens:
            tokens.append(token)
    return tokens


def make_segment(index: int, text: str) -> Segment:
    return Segment(
        id=f"seg-{index:05d}",
        index=index,
        source=text,
        source_hash=sha256_text(text),
        protected_tokens=extract_protected_tokens(text),
    )


def is_reference_heading(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip()).casefold()
    normalized = normalized.rstrip(":")
    return normalized in {"references", "bibliography"}


def mark_segment_types(segments: list[Segment]) -> list[Segment]:
    in_references = False
    marked: list[Segment] = []
    for segment in segments:
        if is_reference_heading(segment.source):
            in_references = True
            segment_type = SEGMENT_REFERENCE_HEADING
        elif in_references:
            segment_type = SEGMENT_REFERENCE_ENTRY
        else:
            segment_type = SEGMENT_BODY
        marked.append(
            Segment(
                id=segment.id,
                index=segment.index,
                source=segment.source,
                source_hash=segment.source_hash,
                protected_tokens=segment.protected_tokens,
                segment_type=segment_type,
            )
        )
    return marked


def segment_to_json(segment: Segment) -> dict[str, Any]:
    return {
        "id": segment.id,
        "index": segment.index,
        "source": segment.source,
        "source_hash": segment.source_hash,
        "protected_tokens": segment.protected_tokens,
        "segment_type": segment.segment_type,
    }


def load_segments(path: Path) -> list[Segment]:
    data = read_json(path)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SkillError(f"Unsupported segments schema in {path}.")
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise SkillError("segments.json must contain at least one segment.")

    segments: list[Segment] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, dict):
            raise SkillError(f"Segment at index {index} must be an object.")
        seg_id = raw.get("id")
        source = raw.get("source")
        source_hash = raw.get("source_hash")
        protected_tokens = raw.get("protected_tokens", [])
        segment_type = raw.get("segment_type", SEGMENT_BODY)
        if not isinstance(seg_id, str) or not seg_id:
            raise SkillError(f"Segment at index {index} has invalid id.")
        if seg_id in seen_ids:
            raise SkillError(f"Duplicate segment id: {seg_id}")
        if not isinstance(source, str) or not source.strip():
            raise SkillError(f"Segment {seg_id} has empty source.")
        if source_hash != sha256_text(source):
            raise SkillError(f"Segment {seg_id} source_hash does not match source.")
        if not isinstance(protected_tokens, list) or not all(
            isinstance(token, str) for token in protected_tokens
        ):
            raise SkillError(f"Segment {seg_id} has invalid protected_tokens.")
        if not isinstance(segment_type, str) or segment_type not in SEGMENT_TYPES:
            raise SkillError(f"Segment {seg_id} has invalid segment_type.")
        seen_ids.add(seg_id)
        segments.append(
            Segment(
                id=seg_id,
                index=int(raw.get("index", index)),
                source=source,
                source_hash=source_hash,
                protected_tokens=protected_tokens,
                segment_type=segment_type,
            )
        )
    return segments


def empty_quality_metrics(target_language: str | None = None) -> dict[str, Any]:
    return {
        "target_language": target_language or "",
        "checked_for_cjk": False,
        "body_segment_count": 0,
        "body_segments_with_cjk": 0,
        "body_segments_without_cjk": [],
        "total_question_marks": 0,
        "segments_with_question_marks": 0,
        "suspicious_question_mark_segments": [],
    }


def is_zh_target(target_language: str | None) -> bool:
    if not target_language:
        return False
    normalized = target_language.strip().replace("_", "-").casefold()
    return normalized == "zh" or normalized.startswith("zh-")


def translation_quality_metrics(
    translations: dict[str, str],
    segments: list[Segment],
    target_language: str | None,
) -> dict[str, Any]:
    metrics = empty_quality_metrics(target_language)
    metrics["checked_for_cjk"] = is_zh_target(target_language)

    suspicious_question_mark_segments: list[str] = []
    body_segments_without_cjk: list[str] = []
    body_segment_count = 0
    body_segments_with_cjk = 0
    total_question_marks = 0
    segments_with_question_marks = 0

    for segment in segments:
        translation = translations.get(segment.id)
        if translation is None:
            continue

        question_marks = translation.count("?")
        total_question_marks += question_marks
        if question_marks:
            segments_with_question_marks += 1
        if question_marks >= QUESTION_MARK_SEGMENT_LIMIT or (
            question_marks >= 5
            and question_marks / max(len(translation), 1) >= QUESTION_MARK_SEGMENT_RATIO
        ):
            suspicious_question_mark_segments.append(segment.id)

        if segment.segment_type == SEGMENT_REFERENCE_ENTRY:
            continue
        body_segment_count += 1
        if _CJK_RE.search(translation):
            body_segments_with_cjk += 1
        else:
            body_segments_without_cjk.append(segment.id)

    metrics["body_segment_count"] = body_segment_count
    metrics["body_segments_with_cjk"] = body_segments_with_cjk
    metrics["body_segments_without_cjk"] = body_segments_without_cjk
    metrics["total_question_marks"] = total_question_marks
    metrics["segments_with_question_marks"] = segments_with_question_marks
    metrics["suspicious_question_mark_segments"] = suspicious_question_mark_segments
    return metrics


def validate_translation_quality(metrics: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    suspicious_ids = metrics["suspicious_question_mark_segments"]
    if metrics["total_question_marks"] > QUESTION_MARK_TOTAL_LIMIT:
        errors.append(
            "translations contain too many question marks "
            f"({metrics['total_question_marks']}); this usually indicates encoding loss"
        )
    if suspicious_ids:
        errors.append(
            "translations contain suspicious question-mark damage in: "
            + ", ".join(suspicious_ids[:20])
        )

    if (
        metrics["checked_for_cjk"]
        and metrics["body_segment_count"] > 0
        and metrics["body_segments_with_cjk"] == 0
    ):
        errors.append(
            "target language is zh but no non-reference translation segment contains CJK characters"
        )
    return errors


def validate_translations(
    path: Path,
    segments: list[Segment],
    job_id: str,
    target_language: str | None = None,
) -> tuple[dict[str, str], list[str], list[str], dict[str, Any]]:
    data = read_json(path)
    errors: list[str] = []
    warnings: list[str] = []
    quality_metrics = empty_quality_metrics(target_language)
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported translations schema in {path}")
    if data.get("job_id") != job_id:
        errors.append(
            f"translations.json job_id {data.get('job_id')!r} does not match {job_id!r}"
        )
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list):
        errors.append("translations.json must contain a segments array")
        return {}, errors, warnings, quality_metrics
    if len(raw_segments) != len(segments):
        errors.append(
            f"segment count mismatch: expected {len(segments)}, got {len(raw_segments)}"
        )

    expected = {segment.id: segment for segment in segments}
    translations: dict[str, str] = {}
    for index, raw in enumerate(raw_segments):
        if not isinstance(raw, dict):
            errors.append(f"translation at index {index} must be an object")
            continue
        seg_id = raw.get("id")
        source = raw.get("source")
        translation = raw.get("translation")
        if not isinstance(seg_id, str) or seg_id not in expected:
            errors.append(f"unknown segment id at index {index}: {seg_id!r}")
            continue
        expected_id = segments[index].id if index < len(segments) else None
        if expected_id is not None and seg_id != expected_id:
            errors.append(
                f"segment order mismatch at index {index}: expected {expected_id}, got {seg_id}"
            )
            continue
        if seg_id in translations:
            errors.append(f"duplicate translation id: {seg_id}")
            continue
        segment = expected[seg_id]
        if source != segment.source:
            errors.append(f"{seg_id}: source mismatch")
            continue
        if not isinstance(translation, str) or not translation.strip():
            errors.append(f"{seg_id}: translation is empty")
            continue
        missing_tokens = [
            token for token in segment.protected_tokens if token not in translation
        ]
        if missing_tokens:
            errors.append(
                f"{seg_id}: translation is missing protected tokens {missing_tokens}"
            )
            continue
        if (
            segment.segment_type == SEGMENT_REFERENCE_ENTRY
            and translation != segment.source
        ):
            errors.append(f"{seg_id}: reference_entry translation must equal source")
            continue
        translations[seg_id] = translation

    missing_ids = [segment.id for segment in segments if segment.id not in translations]
    if missing_ids:
        errors.append(f"missing translations for: {', '.join(missing_ids[:20])}")
    quality_metrics = translation_quality_metrics(
        translations, segments, target_language
    )
    if not missing_ids:
        errors.extend(validate_translation_quality(quality_metrics))
    return translations, errors, warnings, quality_metrics


def load_translation_map(path: Path, segments: list[Segment], job_id: str) -> dict[str, str]:
    translations, errors, _warnings, _quality_metrics = validate_translations(
        path, segments, job_id
    )
    if errors:
        raise SkillError("Invalid translations.json:\n- " + "\n- ".join(errors))
    return translations


class _AgentTranslatorBase:
    name = "agent-base"
    envs: dict[str, Any] = {}
    lang_map: dict[str, str] = {}
    CustomPrompt = False

    def __init__(
        self,
        lang_in: str,
        lang_out: str,
        model: str | None = None,
        envs: dict[str, Any] | None = None,
        prompt: Any = None,
        ignore_cache: bool = False,
        **_: Any,
    ) -> None:
        self.lang_in = lang_in
        self.lang_out = lang_out
        self.model = model or ""
        self.envs = envs or {}

    def translate(self, text: str, ignore_cache: bool = False) -> str:
        return self.do_translate(text)

    def get_rich_text_left_placeholder(self, id: int) -> str:
        return f"<b{id}>"

    def get_rich_text_right_placeholder(self, id: int) -> str:
        return f"</b{id}>"

    def get_formular_placeholder(self, id: int) -> str:
        return f"{{v{id}}}"


def make_capture_translator(captured: list[Segment]):
    class AgentCaptureTranslator(_AgentTranslatorBase):
        name = "agent-capture"

        def do_translate(self, text: str) -> str:
            captured.append(make_segment(len(captured), text))
            return text

    return AgentCaptureTranslator


def make_replay_translator(
    segments: list[Segment], translations: dict[str, str], replayed: list[str]
):
    class AgentReplayTranslator(_AgentTranslatorBase):
        name = "agent-replay"

        def do_translate(self, text: str) -> str:
            position = len(replayed)
            if position >= len(segments):
                raise SkillError(
                    "pdf2zh requested more translated segments than were captured."
                )
            segment = segments[position]
            if text != segment.source:
                raise SkillError(
                    f"Segment order/source mismatch at {segment.id}. "
                    "Run prepare and render with the same pdf2zh version and options."
                )
            replayed.append(segment.id)
            return translations[segment.id]

    return AgentReplayTranslator


def patch_pdf2zh_translators(translator_class: type) -> None:
    converter = importlib.import_module("pdf2zh.converter")
    translator_module = importlib.import_module("pdf2zh.translator")
    setattr(translator_module, translator_class.__name__, translator_class)

    names = [
        "GoogleTranslator",
        "BingTranslator",
        "DeepLTranslator",
        "DeepLXTranslator",
        "OllamaTranslator",
        "XinferenceTranslator",
        "AzureOpenAITranslator",
        "OpenAITranslator",
        "ZhipuTranslator",
        "ModelScopeTranslator",
        "SiliconTranslator",
        "GeminiTranslator",
        "AzureTranslator",
        "TencentTranslator",
        "DifyTranslator",
        "AnythingLLMTranslator",
        "ArgosTranslator",
        "GrokTranslator",
        "GroqTranslator",
        "DeepseekTranslator",
        "OpenAIlikedTranslator",
        "QwenMtTranslator",
    ]
    for name in names:
        obj = getattr(converter, name, None)
        if obj is not None and not hasattr(obj, "_translate_skill_original_name"):
            setattr(obj, "_translate_skill_original_name", getattr(obj, "name", name))
            setattr(obj, "name", f"disabled-{getattr(obj, 'name', name)}")

    alias = "GoogleTranslator"
    previous = getattr(converter, alias, None)
    setattr(converter, "_translate_skill_patched_previous", previous)
    setattr(converter, alias, translator_class)


def run_pdf2zh(
    input_pdf: Path,
    output_dir: Path,
    source_language: str,
    target_language: str,
    service: str,
) -> list[tuple[str, str]]:
    high_level = importlib.import_module("pdf2zh.high_level")
    doclayout = importlib.import_module("pdf2zh.doclayout")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        model = doclayout.OnnxModel.load_available()
        return high_level.translate(
            files=[str(input_pdf)],
            output=str(output_dir),
            lang_in=source_language,
            lang_out=target_language,
            service=service,
            thread=1,
            model=model,
            ignore_cache=True,
        )
    except SkillError:
        raise
    except Exception as exc:
        raise SkillError(f"pdf2zh translation pipeline failed: {exc}") from exc


def run_pdf2zh_with_warnings(
    input_pdf: Path,
    output_dir: Path,
    source_language: str,
    target_language: str,
    service: str,
) -> tuple[list[tuple[str, str]], list[str]]:
    stderr = StringIO()
    with contextlib.redirect_stderr(stderr):
        result_files = run_pdf2zh(
            input_pdf, output_dir, source_language, target_language, service
        )
    warnings = collect_warnings(stderr.getvalue())
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return result_files, warnings


def collect_warnings(stderr_text: str) -> list[str]:
    warnings: list[str] = []
    if "KeyError: 'post'" in stderr_text or 'KeyError: "post"' in stderr_text:
        warnings.append(
            "Font subsetting warning KeyError: 'post' occurred in the PDF pipeline. "
            "If the command succeeded and validation passes, treat it as non-blocking."
        )
    return warnings


def make_default_workdir(input_pdf: Path) -> Path:
    return input_pdf.with_name(f"{input_pdf.stem}.translate-work")


def reference_ranges(segments: list[Segment]) -> list[str]:
    ranges: list[str] = []
    start: Segment | None = None
    previous: Segment | None = None
    for segment in segments:
        if segment.segment_type == SEGMENT_REFERENCE_ENTRY:
            if start is None:
                start = segment
            previous = segment
        elif start is not None and previous is not None:
            ranges.append(f"{start.id} through {previous.id}")
            start = None
            previous = None
    if start is not None and previous is not None:
        ranges.append(f"{start.id} through {previous.id}")
    return ranges


def write_text_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def make_segments_preview(segments: list[Segment]) -> str:
    lines = [
        "# Segments Preview",
        "",
        "Read this UTF-8 markdown file when terminal output makes Unicode ligatures such as `fi`/`fl` look corrupted.",
        "",
    ]
    for segment in segments:
        lines.extend(
            [
                f"## {segment.id}",
                "",
                f"- Type: `{segment.segment_type}`",
                f"- Protected tokens: {', '.join(f'`{token}`' for token in segment.protected_tokens) if segment.protected_tokens else '(none)'}",
                "",
                "Source:",
                "",
                "```text",
                segment.source,
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def translation_issues(segment: Segment, translation: str | None) -> list[str]:
    issues: list[str] = []
    if translation is None:
        issues.append("missing translation")
        return issues
    if not translation.strip():
        issues.append("empty translation")
    missing_tokens = [
        token for token in segment.protected_tokens if token not in translation
    ]
    if missing_tokens:
        issues.append("missing protected tokens: " + ", ".join(missing_tokens))
    if segment.segment_type == SEGMENT_REFERENCE_ENTRY and translation != segment.source:
        issues.append("reference entry translation differs from source")
    return issues


def make_translation_preview(
    segments: list[Segment], translations: dict[str, str]
) -> str:
    lines = [
        "# Translation Preview",
        "",
        "Review this file before delivery. Issues listed here should be fixed before render/verify are trusted.",
        "",
    ]
    for segment in segments:
        translation = translations.get(segment.id)
        issues = translation_issues(segment, translation)
        lines.extend(
            [
                f"## {segment.id}",
                "",
                f"- Type: `{segment.segment_type}`",
                f"- Protected tokens: {', '.join(f'`{token}`' for token in segment.protected_tokens) if segment.protected_tokens else '(none)'}",
                f"- Issues: {', '.join(issues) if issues else '(none)'}",
                "",
                "Source:",
                "",
                "```text",
                segment.source,
                "```",
                "",
                "Translation:",
                "",
                "```text",
                translation or "",
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def output_file_info(path: Path) -> dict[str, Any]:
    exists = path.exists()
    size = path.stat().st_size if exists else 0
    page_count = pdf_page_count(path) if exists and size else None
    return {
        "path": str(path.resolve()),
        "exists": exists,
        "bytes": size,
        "page_count": page_count,
    }


def make_agent_prompt(
    job_id: str,
    source_language: str,
    target_language: str,
    segment_count: int,
    reference_entry_ranges: list[str] | None = None,
) -> str:
    ranges = reference_entry_ranges or []
    reference_note = (
        "Reference entries detected: "
        + "; ".join(ranges)
        + ". Copy each source to translation unchanged."
        if ranges
        else "No reference-entry range was detected automatically."
    )
    return f"""# Agent Translation Task

Translate `segments.json` into `translations.json` for job `{job_id}`.

Rules:
- Translate from `{source_language}` to `{target_language}` in faithful academic style.
- Output JSON only. Do not include markdown fences or commentary.
- Preserve every `protected_tokens` value exactly in the corresponding translation.
- Do not translate protected numbering. Keep `Eq. 6`, `Figure 1`, and `Table 2` exactly; do not change them to translated forms such as `公式 6`, `图 1`, or `表 2`.
- Preserve formula placeholders such as `{{{{v0}}}}`, `{{v0}}`, `<b0>`, `</b0>`, and citation/figure/table numbering.
- Keep English bibliography/reference entries unchanged. If a segment is a bibliography or reference-list entry, copy its `source` text directly into `translation`.
- {reference_note}
- Keep English author names, initials, and Latin-script personal names unchanged. Do not transliterate, localize, or translate author names.
- Keep DOI, URL, journal names, conference names, publisher names, page ranges, volume/issue identifiers, and publication metadata unchanged.
- Keep the same segment ids and copy each `source` field exactly.
- Do not merge, split, reorder, omit, or duplicate segments.

Required output schema:
{{
  "schema_version": "{SCHEMA_VERSION}",
  "job_id": "{job_id}",
  "segments": [
    {{
      "id": "seg-00000",
      "source": "original source text copied exactly",
      "translation": "translated text with protected tokens preserved"
    }}
  ]
}}

Segment count: {segment_count}
"""


def command_prepare(args: argparse.Namespace) -> int:
    input_pdf = Path(args.input_pdf).resolve()
    ensure_real_pdf(input_pdf)
    ensure_pdf2zh_available()

    workdir = Path(args.out).resolve() if args.out else make_default_workdir(input_pdf)
    output_dir = workdir / "capture-output"
    captured: list[Segment] = []
    patch_pdf2zh_translators(make_capture_translator(captured))

    result_files, warnings = run_pdf2zh_with_warnings(
        input_pdf,
        output_dir,
        args.source,
        args.target,
        "agent-capture",
    )
    if not captured:
        raise SkillError(
            "pdf2zh did not expose any translatable segments. "
            "Check that the PDF contains selectable text and is not image-only."
        )
    captured = mark_segment_types(captured)

    job_id = uuid.uuid4().hex
    job = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "input_pdf": str(input_pdf),
        "source_language": args.source,
        "target_language": args.target,
        "pdf2zh_service": "agent-capture/agent-replay",
        "segment_count": len(captured),
        "expected_outputs": {
            "mono_pdf": str(workdir / "output" / f"{input_pdf.stem}-mono.pdf"),
            "dual_pdf": str(workdir / "output" / f"{input_pdf.stem}-dual.pdf"),
        },
    }
    segments_json = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "source_language": args.source,
        "target_language": args.target,
        "segments": [segment_to_json(segment) for segment in captured],
    }
    workdir.mkdir(parents=True, exist_ok=True)
    write_json(workdir / "job.json", job)
    write_json(workdir / "segments.json", segments_json)
    write_text_file(
        workdir / "agent_prompt.md",
        make_agent_prompt(
            job_id,
            args.source,
            args.target,
            len(captured),
            reference_ranges(captured),
        ),
    )
    write_text_file(workdir / "segments-preview.md", make_segments_preview(captured))
    write_json(
        workdir / "prepare-report.json",
        {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "captured_segments": len(captured),
            "capture_output_files": result_files,
            "warnings": warnings,
            "next_step": "Use the current agent model to translate segments.json into translations.json, then run render.",
        },
    )
    print(f"Prepared {len(captured)} segments in {workdir}")
    print(f"Next: create {workdir / 'translations.json'} from segments.json.")
    print(f"Then validate with: {Path(__file__).name} validate {workdir}")
    return 0


def validation_report_data(
    job: dict[str, Any],
    segments: list[Segment],
    translations_path: Path,
    errors: list[str],
    warnings: list[str],
    quality_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    type_counts = {segment_type: 0 for segment_type in sorted(SEGMENT_TYPES)}
    for segment in segments:
        type_counts[segment.segment_type] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "job_id": job.get("job_id"),
        "ok": not errors,
        "translations_path": str(translations_path.resolve()),
        "segment_count": len(segments),
        "segment_type_counts": type_counts,
        "translation_quality": quality_metrics
        or empty_quality_metrics(str(job.get("target_language", ""))),
        "errors": errors,
        "warnings": warnings,
    }


def write_validation_report(
    workdir: Path,
    job: dict[str, Any],
    segments: list[Segment],
    translations_path: Path,
    errors: list[str],
    warnings: list[str],
    quality_metrics: dict[str, Any] | None = None,
) -> None:
    write_json(
        workdir / "validate-report.json",
        validation_report_data(
            job,
            segments,
            translations_path,
            errors,
            warnings,
            quality_metrics,
        ),
    )


def command_validate(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    job = read_json(workdir / "job.json")
    segments = load_segments(workdir / "segments.json")
    translations_path = (
        Path(args.translations).resolve()
        if args.translations
        else workdir / "translations.json"
    )
    translations, errors, warnings, quality_metrics = validate_translations(
        translations_path,
        segments,
        str(job.get("job_id")),
        str(job.get("target_language", DEFAULT_TARGET_LANGUAGE)),
    )
    write_validation_report(
        workdir,
        job,
        segments,
        translations_path,
        errors,
        warnings,
        quality_metrics,
    )
    if translations:
        write_text_file(
            workdir / "translation-preview.md",
            make_translation_preview(segments, translations),
        )
    if errors:
        raise SkillError("Validation failed:\n- " + "\n- ".join(errors))
    print(f"Validation passed. Report: {workdir / 'validate-report.json'}")
    print(f"Preview: {workdir / 'translation-preview.md'}")
    return 0


def command_render(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    job = read_json(workdir / "job.json")
    if job.get("schema_version") != SCHEMA_VERSION:
        raise SkillError("Unsupported job.json schema.")
    input_pdf = Path(str(job.get("input_pdf", ""))).resolve()
    ensure_real_pdf(input_pdf)
    ensure_pdf2zh_available()

    segments = load_segments(workdir / "segments.json")
    translations_path = (
        Path(args.translations).resolve()
        if args.translations
        else workdir / "translations.json"
    )
    (
        translations,
        validation_errors,
        validation_warnings,
        quality_metrics,
    ) = validate_translations(
        translations_path,
        segments,
        str(job.get("job_id")),
        str(job.get("target_language", DEFAULT_TARGET_LANGUAGE)),
    )
    if validation_errors:
        write_validation_report(
            workdir,
            job,
            segments,
            translations_path,
            validation_errors,
            validation_warnings,
            quality_metrics,
        )
        raise SkillError("Invalid translations.json:\n- " + "\n- ".join(validation_errors))

    output_dir = workdir / "output"
    replayed: list[str] = []
    patch_pdf2zh_translators(make_replay_translator(segments, translations, replayed))
    result_files, render_warnings = run_pdf2zh_with_warnings(
        input_pdf,
        output_dir,
        str(job.get("source_language", DEFAULT_SOURCE_LANGUAGE)),
        str(job.get("target_language", DEFAULT_TARGET_LANGUAGE)),
        "agent-replay",
    )
    if len(replayed) != len(segments):
        raise SkillError(
            f"pdf2zh replayed {len(replayed)} segments, expected {len(segments)}."
        )
    write_text_file(
        workdir / "translation-preview.md",
        make_translation_preview(segments, translations),
    )
    write_json(
        workdir / "render-report.json",
        {
            "schema_version": SCHEMA_VERSION,
            "job_id": job.get("job_id"),
            "rendered_segments": len(replayed),
            "output_files": result_files,
            "outputs": {
                label: output_file_info(path)
                for label, path in {
                    "mono_pdf": output_dir / f"{input_pdf.stem}-mono.pdf",
                    "dual_pdf": output_dir / f"{input_pdf.stem}-dual.pdf",
                }.items()
            },
            "warnings": validation_warnings + render_warnings,
        },
    )
    print(f"Rendered translated PDFs in {output_dir}")
    return 0


def pdf_page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader  # type: ignore
    except ImportError:
        return None
    try:
        return len(PdfReader(str(path)).pages)
    except Exception as exc:
        raise SkillError(f"Output PDF cannot be parsed: {path}: {exc}") from exc


def command_verify(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    job = read_json(workdir / "job.json")
    segments = load_segments(workdir / "segments.json")
    translations_path = workdir / "translations.json"
    translations_valid = translations_path.exists()
    warnings: list[str] = []
    if translations_valid:
        (
            _translations,
            validation_errors,
            validation_warnings,
            _quality_metrics,
        ) = validate_translations(
            translations_path,
            segments,
            str(job.get("job_id")),
            str(job.get("target_language", DEFAULT_TARGET_LANGUAGE)),
        )
        warnings.extend(validation_warnings)
        if validation_errors:
            raise SkillError(
                "Invalid translations.json:\n- " + "\n- ".join(validation_errors)
            )

    stem = Path(str(job.get("input_pdf", "paper.pdf"))).stem
    mono_pdf = workdir / "output" / f"{stem}-mono.pdf"
    dual_pdf = workdir / "output" / f"{stem}-dual.pdf"
    outputs: dict[str, Any] = {}
    errors: list[str] = []
    for label, path in {"mono_pdf": mono_pdf, "dual_pdf": dual_pdf}.items():
        info = output_file_info(path)
        outputs[label] = info
        exists = bool(info["exists"])
        size = int(info["bytes"])
        if not exists:
            errors.append(f"Missing output: {path}")
        elif size == 0:
            errors.append(f"Output is empty: {path}")

    for report_name in ("prepare-report.json", "render-report.json"):
        report_path = workdir / report_name
        if report_path.exists():
            report = read_json(report_path)
            report_warnings = report.get("warnings", [])
            if isinstance(report_warnings, list):
                warnings.extend(
                    warning for warning in report_warnings if isinstance(warning, str)
                )

    report = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job.get("job_id"),
        "ok": not errors,
        "segment_count": len(segments),
        "translations_json_present": translations_valid,
        "outputs": outputs,
        "warnings": warnings,
        "errors": errors,
    }
    write_json(workdir / "report.json", report)
    if errors:
        raise SkillError("Verification failed:\n- " + "\n- ".join(errors))
    print(f"Verification passed. Report: {workdir / 'report.json'}")
    return 0


def command_make_template(args: argparse.Namespace) -> int:
    workdir = Path(args.workdir).resolve()
    job = read_json(workdir / "job.json")
    segments = load_segments(workdir / "segments.json")
    data = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job.get("job_id"),
        "segments": [
            {
                "id": segment.id,
                "source": segment.source,
                "translation": segment.source,
            }
            for segment in segments
        ],
    }
    output = Path(args.output).resolve() if args.output else workdir / "translations.json"
    write_json(output, data)
    print(f"Wrote translation template: {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Two-pass agent bridge for pdf2zh layout-preserving paper translation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="Capture translatable PDF segments for agent translation."
    )
    prepare.add_argument("input_pdf", help="Path to source paper PDF.")
    prepare.add_argument("--out", help="Work directory. Default: <input-stem>.translate-work")
    prepare.add_argument("--source", default=DEFAULT_SOURCE_LANGUAGE)
    prepare.add_argument("--target", default=DEFAULT_TARGET_LANGUAGE)
    prepare.set_defaults(func=command_prepare)

    render = subparsers.add_parser(
        "render", help="Render translated mono/dual PDFs from translations.json."
    )
    render.add_argument("workdir", help="Work directory created by prepare.")
    render.add_argument(
        "--translations",
        help="Path to translations.json. Default: <workdir>/translations.json",
    )
    render.set_defaults(func=command_render)

    validate = subparsers.add_parser(
        "validate", help="Validate translations.json before rendering."
    )
    validate.add_argument("workdir", help="Work directory created by prepare.")
    validate.add_argument(
        "--translations",
        help="Path to translations.json. Default: <workdir>/translations.json",
    )
    validate.set_defaults(func=command_validate)

    verify = subparsers.add_parser(
        "verify", help="Validate translated outputs and write report.json."
    )
    verify.add_argument("workdir", help="Work directory created by prepare.")
    verify.set_defaults(func=command_verify)

    template = subparsers.add_parser(
        "make-template",
        help="Create a source-copy translations.json template for testing.",
    )
    template.add_argument("workdir", help="Work directory created by prepare.")
    template.add_argument("--output", help="Output JSON path.")
    template.set_defaults(func=command_make_template)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SkillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
