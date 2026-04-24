import sys
import types
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import services.llm_factory as llm_factory


class LlmFactoryTests(unittest.TestCase):
    def test_infer_provider_detects_ollama_from_provider_alias(self):
        provider = llm_factory.infer_provider(
            model="qwen2.5:1.5b",
            base_url="",
            provider="ollama",
            api_key="",
        )

        self.assertEqual(provider, "ollama")

    def test_infer_provider_detects_ollama_from_base_url(self):
        provider = llm_factory.infer_provider(
            model="qwen2.5:1.5b",
            base_url="http://127.0.0.1:11434",
            provider="",
            api_key="",
        )

        self.assertEqual(provider, "ollama")

    def test_infer_provider_does_not_use_api_key_prefix(self):
        provider = llm_factory.infer_provider(
            model="deepseek-chat",
            base_url="",
            provider="",
            api_key="sk-any-provider-key",
        )

        self.assertEqual(provider, "deepseek")

    def test_get_provider_audio_defaults_returns_empty_values_for_ollama(self):
        defaults = llm_factory.get_provider_audio_defaults("ollama")

        self.assertEqual(
            defaults,
            {"tts_model": "", "tts_voice": "", "stt_model": ""},
        )

    def test_create_chat_model_uses_ollama_defaults(self):
        captured_kwargs = {}

        class _FakeChatOpenAI:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

        fake_module = types.SimpleNamespace(ChatOpenAI=_FakeChatOpenAI)

        with mock.patch.dict(sys.modules, {"langchain_openai": fake_module}):
            chat = llm_factory.create_chat_model(
                provider="ollama",
                model="qwen2.5:1.5b",
                api_key="",
                base_url="",
                temperature=0,
                max_tokens=128,
                timeout=12,
                max_retries=1,
            )

        self.assertIsInstance(chat, _FakeChatOpenAI)
        self.assertEqual(captured_kwargs["model"], "qwen2.5:1.5b")
        self.assertEqual(captured_kwargs["base_url"], "http://127.0.0.1:11434/v1")
        self.assertEqual(captured_kwargs["api_key"], "ollama")
        self.assertEqual(captured_kwargs["temperature"], 0)
        self.assertEqual(captured_kwargs["max_tokens"], 128)
        self.assertEqual(captured_kwargs["timeout"], 12)
        self.assertEqual(captured_kwargs["max_retries"], 1)
        self.assertNotIn("use_responses_api", captured_kwargs)


if __name__ == "__main__":
    unittest.main()
