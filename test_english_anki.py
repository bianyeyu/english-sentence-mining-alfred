#!/usr/bin/env python3
import json
import unittest
from unittest.mock import patch

import english_anki


class TargetExtractionTests(unittest.TestCase):
    def test_extract_targets_includes_words_and_phrases(self):
        sentence = "We need to take into account the trade off."

        targets = {(item["type"], item["text"].lower()) for item in english_anki.extract_targets(sentence)}

        self.assertIn(("word", "account"), targets)
        self.assertIn(("phrase", "take into account"), targets)

    def test_slash_query_is_not_treated_as_new_sentence(self):
        self.assertFalse(english_anki.looks_like_sentence_input("/in light of"))

    def test_cloze_sentence_can_highlight_phrase(self):
        cloze = english_anki.cloze_sentence("We acted in light of the evidence.", "in light of")

        self.assertIn("<b>in light of</b>", cloze)

    def test_llm_lookup_uses_configured_openai_compatible_api(self):
        config = dict(english_anki.DEFAULT_CONFIG)
        config.update({
            "llm_api_key": "test-key",
            "llm_base_url": "https://api.groq.com/openai/v1",
            "llm_model": "demo-model",
        })
        content = {
            "word": "useful",
            "lemma": "useful",
            "part_of_speech": "adjective",
            "word_meaning_zh": "有用的",
            "meaning_in_context_zh": "有帮助的",
            "sentence_translation_zh": "这是一个有用的测试。",
            "cloze_sentence": "This is a <b>useful</b> test.",
        }
        response = {"choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}]}

        with patch("english_anki.request_json", return_value=response) as request:
            result = english_anki.lookup_llm(config, "useful", "This is a useful test.")

        url, payload = request.call_args.args[:2]
        self.assertEqual(url, "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(payload["model"], "demo-model")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(result["meaning_in_context_zh"], "有帮助的")
        self.assertEqual(result["lookup_note"], "llm_api: demo-model")

    def test_llm_connectivity_test_returns_model_and_host(self):
        config = dict(english_anki.DEFAULT_CONFIG)
        config.update({
            "llm_api_key": "test-key",
            "llm_base_url": "https://api.groq.com/openai/v1",
            "llm_model": "openai/gpt-oss-120b",
        })
        response = {"choices": [{"message": {"content": '{"ok": true, "message": "pong"}'}}]}

        with patch("english_anki.request_json", return_value=response):
            message = english_anki.test_llm_connection(config)

        self.assertIn("openai/gpt-oss-120b", message)
        self.assertIn("api.groq.com", message)

    def test_translation_item_shows_sentence_translation(self):
        config = dict(english_anki.DEFAULT_CONFIG)
        sentence = "This is a useful test."

        with patch("english_anki.get_cached_lookup", return_value="这是一个有用的测试。"):
            item, pending = english_anki.translation_item(config, sentence, sentence)

        self.assertEqual(item["title"], "整句翻译：这是一个有用的测试。")
        self.assertFalse(item["valid"])
        self.assertFalse(pending)

    def test_translation_item_respects_preview_disabled(self):
        config = dict(english_anki.DEFAULT_CONFIG)
        config["preview_translation"] = False

        item, pending = english_anki.translation_item(config, "No preview needed.", "No preview needed.")

        self.assertEqual(item["title"], "当前句子已缓存")
        self.assertFalse(pending)

    def test_two_letter_word_prefix_can_start_target_lookup(self):
        config = dict(english_anki.DEFAULT_CONFIG)

        self.assertTrue(english_anki.should_allow_target_lookup(config, "te", "test", "word"))
        self.assertFalse(english_anki.should_allow_target_lookup(config, "t", "test", "word"))
        self.assertFalse(english_anki.should_allow_target_lookup(config, "es", "test", "word"))
        self.assertFalse(english_anki.should_allow_target_lookup(config, "te", "test case", "phrase"))


if __name__ == "__main__":
    unittest.main()
