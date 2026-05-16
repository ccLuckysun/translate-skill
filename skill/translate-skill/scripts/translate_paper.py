#!/usr/bin/env python3
"""
Two-pass agent translation bridge for layout-preserving paper translation.

pdf2zh/PDFMathTranslate owns PDF parsing, formula protection, layout rebuild,
and mono/dual PDF export. The Codex agent owns the actual translation by
turning segments.json into translations.json between prepare and render.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
import sys
import types
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "translate-skill.agent.v1"
DEFAULT_SOURCE_LANGUAGE = "en"
DEFAULT_TARGET_LANGUAGE = "zh"
PDF2ZH_VERSION_PREFIX = "1.9."

_TOKEN_RE = re.compile(
    r"(\{v\d+\}|<b\d+>|</b\d+>|\{\{v\d+\}\}|\[[0-9,\-\s]+\]|"
    r"\([A-Za-z][A-Za-z0-9_.-]*,\s*\d{4}[a-z]?\)|"
    r"\b(?:Fig|Figure|Table|Eq|Equation|Sec|Section)\.?\s*\d+(?:\.\d+)*)"
)


class SkillError(RuntimeError):
    """Expected user-facing failure."""


@dataclass(frozen=True)
class Segment:
    id: str
    index: int
    source: str
    source_hash: str
    protected_tokens: list[str]


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
            "Install it with: python -m pip install 'pdf2zh>=1.9,<1.10'"
        ) from exc

    version = getattr(pdf2zh, "__version__", "")
    if not str(version).startswith(PDF2ZH_VERSION_PREFIX):
        raise SkillError(
            f"Unsupported pdf2zh version {version!r}. "
            "This skill targets pdf2zh 1.9.x; install with: "
            "python -m pip install 'pdf2zh>=1.9,<1.10'"
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


def segment_to_json(segment: Segment) -> dict[str, Any]:
    return {
        "id": segment.id,
        "index": segment.index,
        "source": segment.source,
        "source_hash": segment.source_hash,
        "protected_tokens": segment.protected_tokens,
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
        seen_ids.add(seg_id)
        segments.append(
            Segment(
                id=seg_id,
                index=int(raw.get("index", index)),
                source=source,
                source_hash=source_hash,
                protected_tokens=protected_tokens,
            )
        )
    return segments


def load_translation_map(path: Path, segments: list[Segment], job_id: str) -> dict[str, str]:
    data = read_json(path)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SkillError(f"Unsupported translations schema in {path}.")
    if data.get("job_id") != job_id:
        raise SkillError(
            f"translations.json job_id {data.get('job_id')!r} does not match {job_id!r}."
        )
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list):
        raise SkillError("translations.json must contain a segments array.")

    expected = {segment.id: segment for segment in segments}
    translations: dict[str, str] = {}
    errors: list[str] = []
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
        translations[seg_id] = translation

    missing_ids = [segment.id for segment in segments if segment.id not in translations]
    if missing_ids:
        errors.append(f"missing translations for: {', '.join(missing_ids[:20])}")
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


def make_default_workdir(input_pdf: Path) -> Path:
    return input_pdf.with_name(f"{input_pdf.stem}.translate-work")


def make_agent_prompt(
    job_id: str,
    source_language: str,
    target_language: str,
    segment_count: int,
) -> str:
    return f"""# Agent Translation Task

Translate `segments.json` into `translations.json` for job `{job_id}`.

Rules:
- Translate from `{source_language}` to `{target_language}` in faithful academic style.
- Output JSON only. Do not include markdown fences or commentary.
- Preserve every `protected_tokens` value exactly in the corresponding translation.
- Preserve formula placeholders such as `{{{{v0}}}}`, `{{v0}}`, `<b0>`, `</b0>`, and citation/figure/table numbering.
- Keep English bibliography/reference entries unchanged. If a segment is a bibliography or reference-list entry, copy its `source` text directly into `translation`.
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

    result_files = run_pdf2zh(
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
    (workdir / "agent_prompt.md").write_text(
        make_agent_prompt(job_id, args.source, args.target, len(captured)),
        encoding="utf-8",
        newline="\n",
    )
    write_json(
        workdir / "prepare-report.json",
        {
            "schema_version": SCHEMA_VERSION,
            "job_id": job_id,
            "captured_segments": len(captured),
            "capture_output_files": result_files,
            "next_step": "Use the current agent model to translate segments.json into translations.json, then run render.",
        },
    )
    print(f"Prepared {len(captured)} segments in {workdir}")
    print(f"Next: create {workdir / 'translations.json'} from segments.json.")
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
    translations = load_translation_map(
        translations_path, segments, str(job.get("job_id"))
    )

    output_dir = workdir / "output"
    replayed: list[str] = []
    patch_pdf2zh_translators(make_replay_translator(segments, translations, replayed))
    result_files = run_pdf2zh(
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
    write_json(
        workdir / "render-report.json",
        {
            "schema_version": SCHEMA_VERSION,
            "job_id": job.get("job_id"),
            "rendered_segments": len(replayed),
            "output_files": result_files,
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
    if translations_valid:
        load_translation_map(translations_path, segments, str(job.get("job_id")))

    stem = Path(str(job.get("input_pdf", "paper.pdf"))).stem
    mono_pdf = workdir / "output" / f"{stem}-mono.pdf"
    dual_pdf = workdir / "output" / f"{stem}-dual.pdf"
    outputs: dict[str, Any] = {}
    errors: list[str] = []
    for label, path in {"mono_pdf": mono_pdf, "dual_pdf": dual_pdf}.items():
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        page_count = pdf_page_count(path) if exists and size else None
        outputs[label] = {
            "path": str(path),
            "exists": exists,
            "bytes": size,
            "page_count": page_count,
        }
        if not exists:
            errors.append(f"Missing output: {path}")
        elif size == 0:
            errors.append(f"Output is empty: {path}")

    report = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job.get("job_id"),
        "ok": not errors,
        "segment_count": len(segments),
        "translations_json_present": translations_valid,
        "outputs": outputs,
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
