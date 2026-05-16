from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "translate_paper.py"
)
spec = importlib.util.spec_from_file_location("translate_paper", SCRIPT_PATH)
translate_paper = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = translate_paper
spec.loader.exec_module(translate_paper)


class TranslationMapTests(unittest.TestCase):
    def test_rejects_missing_protected_token(self) -> None:
        segment = translate_paper.make_segment(0, "See Eq. 1 and {v0}.")
        translations = {
            "schema_version": translate_paper.SCHEMA_VERSION,
            "job_id": "job",
            "segments": [
                {
                    "id": segment.id,
                    "source": segment.source,
                    "translation": "See equation 1 and {v0}.",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translations.json"
            translate_paper.write_json(path, translations)
            with self.assertRaisesRegex(
                translate_paper.SkillError, "protected tokens"
            ):
                translate_paper.load_translation_map(path, [segment], "job")

    def test_rejects_source_mismatch(self) -> None:
        segment = translate_paper.make_segment(0, "Original text.")
        translations = {
            "schema_version": translate_paper.SCHEMA_VERSION,
            "job_id": "job",
            "segments": [
                {
                    "id": segment.id,
                    "source": "Changed text.",
                    "translation": "Translated text.",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translations.json"
            translate_paper.write_json(path, translations)
            with self.assertRaisesRegex(translate_paper.SkillError, "source mismatch"):
                translate_paper.load_translation_map(path, [segment], "job")

    def test_accepts_valid_translation(self) -> None:
        segment = translate_paper.make_segment(0, "See Fig. 2 and {v1}.")
        translations = {
            "schema_version": translate_paper.SCHEMA_VERSION,
            "job_id": "job",
            "segments": [
                {
                    "id": segment.id,
                    "source": segment.source,
                    "translation": "See Fig. 2 and {v1}.",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translations.json"
            translate_paper.write_json(path, translations)
            self.assertEqual(
                translate_paper.load_translation_map(path, [segment], "job"),
                {segment.id: "See Fig. 2 and {v1}."},
            )

    def test_rejects_order_mismatch(self) -> None:
        first = translate_paper.make_segment(0, "First segment.")
        second = translate_paper.make_segment(1, "Second segment.")
        translations = {
            "schema_version": translate_paper.SCHEMA_VERSION,
            "job_id": "job",
            "segments": [
                {
                    "id": second.id,
                    "source": second.source,
                    "translation": "Second translation.",
                },
                {
                    "id": first.id,
                    "source": first.source,
                    "translation": "First translation.",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translations.json"
            translate_paper.write_json(path, translations)
            with self.assertRaisesRegex(translate_paper.SkillError, "order mismatch"):
                translate_paper.load_translation_map(path, [first, second], "job")

    def test_rejects_reference_entry_translation(self) -> None:
        source = "[1] Smith, J. A paper. 2020."
        segment = translate_paper.Segment(
            id="seg-00000",
            index=0,
            source=source,
            source_hash=translate_paper.sha256_text(source),
            protected_tokens=[],
            segment_type=translate_paper.SEGMENT_REFERENCE_ENTRY,
        )
        translations = {
            "schema_version": translate_paper.SCHEMA_VERSION,
            "job_id": "job",
            "segments": [
                {
                    "id": segment.id,
                    "source": segment.source,
                    "translation": "[1] Smith, J. Translated title. 2020.",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translations.json"
            translate_paper.write_json(path, translations)
            with self.assertRaisesRegex(
                translate_paper.SkillError, "reference_entry"
            ):
                translate_paper.load_translation_map(path, [segment], "job")

    def test_validate_command_writes_report_and_preview(self) -> None:
        segment = translate_paper.make_segment(0, "See Eq. 6.")
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            translate_paper.write_json(
                workdir / "job.json",
                {
                    "schema_version": translate_paper.SCHEMA_VERSION,
                    "job_id": "job",
                    "input_pdf": str(workdir / "paper.pdf"),
                },
            )
            translate_paper.write_json(
                workdir / "segments.json",
                {
                    "schema_version": translate_paper.SCHEMA_VERSION,
                    "job_id": "job",
                    "segments": [translate_paper.segment_to_json(segment)],
                },
            )
            translate_paper.write_json(
                workdir / "translations.json",
                {
                    "schema_version": translate_paper.SCHEMA_VERSION,
                    "job_id": "job",
                    "segments": [
                        {
                            "id": segment.id,
                            "source": segment.source,
                            "translation": "See Eq. 6.",
                        }
                    ],
                },
            )

            args = type("Args", (), {"workdir": str(workdir), "translations": None})()
            self.assertEqual(translate_paper.command_validate(args), 0)
            report = translate_paper.read_json(workdir / "validate-report.json")
            self.assertTrue(report["ok"])
            self.assertTrue((workdir / "translation-preview.md").exists())


class ReplayTranslatorTests(unittest.TestCase):
    def test_requires_same_order_and_source(self) -> None:
        segment = translate_paper.make_segment(0, "A source segment.")
        replayed: list[str] = []
        cls = translate_paper.make_replay_translator(
            [segment], {segment.id: "Translated segment."}, replayed
        )
        translator = cls("en", "zh")

        self.assertEqual(translator.translate(segment.source), "Translated segment.")
        self.assertEqual(replayed, [segment.id])

    def test_rejects_order_mismatch(self) -> None:
        segment = translate_paper.make_segment(0, "A source segment.")
        replayed: list[str] = []
        cls = translate_paper.make_replay_translator(
            [segment], {segment.id: "Translated segment."}, replayed
        )
        translator = cls("en", "zh")

        with self.assertRaisesRegex(translate_paper.SkillError, "mismatch"):
            translator.translate("Different source.")


class AgentPromptTests(unittest.TestCase):
    def test_prompt_preserves_references_and_author_names(self) -> None:
        prompt = translate_paper.make_agent_prompt(
            "job", "en", "zh", 3, ["seg-00117 through seg-00134"]
        )

        self.assertIn("Keep English bibliography/reference entries unchanged", prompt)
        self.assertIn("copy its `source` text directly into `translation`", prompt)
        self.assertIn("Keep English author names", prompt)
        self.assertIn("Do not transliterate", prompt)
        self.assertIn("Keep `Eq. 6`, `Figure 1`, and `Table 2` exactly", prompt)
        self.assertIn("seg-00117 through seg-00134", prompt)


class SegmentTypeTests(unittest.TestCase):
    def test_marks_references_after_heading(self) -> None:
        segments = [
            translate_paper.make_segment(0, "Introduction"),
            translate_paper.make_segment(1, "References"),
            translate_paper.make_segment(2, "[1] Smith, J. A paper. 2020."),
        ]

        marked = translate_paper.mark_segment_types(segments)

        self.assertEqual(marked[0].segment_type, translate_paper.SEGMENT_BODY)
        self.assertEqual(
            marked[1].segment_type, translate_paper.SEGMENT_REFERENCE_HEADING
        )
        self.assertEqual(
            marked[2].segment_type, translate_paper.SEGMENT_REFERENCE_ENTRY
        )


if __name__ == "__main__":
    unittest.main()
