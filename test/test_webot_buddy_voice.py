import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if "langchain_core.language_models.chat_models" not in sys.modules:
    chat_models_module = type(sys)("langchain_core.language_models.chat_models")

    class BaseChatModel:
        pass

    chat_models_module.BaseChatModel = BaseChatModel
    sys.modules["langchain_core.language_models.chat_models"] = chat_models_module

import webot.runtime_store as runtime_store
from webot.buddy import apply_buddy_action, ensure_buddy_state, get_buddy_state
from webot.voice import get_voice_state


class WeBotBuddyTests(unittest.TestCase):
    def test_buddy_state_is_deterministic_and_actions_persist(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                first = ensure_buddy_state("alice")
                second = ensure_buddy_state("alice")
                other = ensure_buddy_state("bob")

                updated = apply_buddy_action("alice", "bridge", note="viewer attached")
                persisted = runtime_store.get_buddy_state("alice")
                serialized = get_buddy_state("alice")

                self.assertEqual(first.seed, second.seed)
                self.assertEqual(first.species, second.species)
                self.assertEqual(first.hatched_at, second.hatched_at)
                self.assertNotEqual(first.seed, other.seed)
                self.assertIn("waves at the remote viewer", updated["reaction"])
                self.assertIn("viewer attached", updated["reaction"])
                self.assertEqual(persisted.reaction, updated["reaction"])
                self.assertEqual(serialized["species"], first.species)
                self.assertTrue(serialized["compact_face"])
                self.assertIn("bridge", serialized["available_actions"])
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path


class WeBotVoiceTests(unittest.TestCase):
    def test_voice_state_uses_provider_defaults_and_persisted_overrides(self):
        with TemporaryDirectory() as tmpdir:
            original_runtime_db_path = runtime_store.DEFAULT_DB_PATH
            runtime_store.DEFAULT_DB_PATH = Path(tmpdir) / "runtime.db"
            try:
                with patch.dict(
                    os.environ,
                    {
                        "LLM_PROVIDER": "google",
                        "LLM_MODEL": "gemini-2.5-flash",
                        "LLM_BASE_URL": "https://generativelanguage.googleapis.com",
                        "LLM_API_KEY": "AIza-test-key",
                        "TTS_MODEL": "",
                        "TTS_VOICE": "",
                        "STT_MODEL": "",
                    },
                    clear=False,
                ):
                    default_state = get_voice_state("alice", "default")
                    self.assertEqual(default_state["provider"], "google")
                    self.assertEqual(default_state["tts_model"], "gemini-2.5-flash-preview-tts")
                    self.assertEqual(default_state["tts_voice"], "charon")
                    self.assertEqual(default_state["stt_model"], "")
                    self.assertFalse(default_state["enabled"])

                    runtime_store.save_voice_state(
                        "alice",
                        "default",
                        enabled=True,
                        auto_read_aloud=True,
                        tts_model="custom-tts",
                        tts_voice="nova",
                        stt_model="custom-stt",
                        last_transcript="ship it",
                        metadata={"source": "test"},
                    )
                    overridden = get_voice_state("alice", "default")

                self.assertTrue(overridden["enabled"])
                self.assertTrue(overridden["auto_read_aloud"])
                self.assertEqual(overridden["tts_model"], "custom-tts")
                self.assertEqual(overridden["tts_voice"], "nova")
                self.assertEqual(overridden["stt_model"], "custom-stt")
                self.assertEqual(overridden["last_transcript"], "ship it")
                self.assertEqual(overridden["metadata"]["source"], "test")
            finally:
                runtime_store.DEFAULT_DB_PATH = original_runtime_db_path


if __name__ == "__main__":
    unittest.main()
