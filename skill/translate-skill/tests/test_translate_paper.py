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
                    "translation": "参见公式 1。",
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
                    "translation": "译文。",
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
                    "translation": "参见 Fig. 2 和 {v1}。",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "translations.json"
            translate_paper.write_json(path, translations)
            self.assertEqual(
                translate_paper.load_translation_map(path, [segment], "job"),
                {segment.id: "参见 Fig. 2 和 {v1}。"},
            )


class ReplayTranslatorTests(unittest.TestCase):
    def test_requires_same_order_and_source(self) -> None:
        segment = translate_paper.make_segment(0, "A source segment.")
        replayed: list[str] = []
        cls = translate_paper.make_replay_translator(
            [segment], {segment.id: "译文。"}, replayed
        )
        translator = cls("en", "zh")

        self.assertEqual(translator.translate(segment.source), "译文。")
        self.assertEqual(replayed, [segment.id])

    def test_rejects_order_mismatch(self) -> None:
        segment = translate_paper.make_segment(0, "A source segment.")
        replayed: list[str] = []
        cls = translate_paper.make_replay_translator(
            [segment], {segment.id: "译文。"}, replayed
        )
        translator = cls("en", "zh")

        with self.assertRaisesRegex(translate_paper.SkillError, "mismatch"):
            translator.translate("Different source.")


class AgentPromptTests(unittest.TestCase):
    def test_prompt_preserves_references_and_author_names(self) -> None:
        prompt = translate_paper.make_agent_prompt("job", "en", "zh", 3)

        self.assertIn("Keep English bibliography/reference entries unchanged", prompt)
        self.assertIn("copy its `source` text directly into `translation`", prompt)
        self.assertIn("Keep English author names", prompt)
        self.assertIn("Do not transliterate", prompt)


if __name__ == "__main__":
    unittest.main()
