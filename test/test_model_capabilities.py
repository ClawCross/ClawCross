"""Capability precedence, unknown models, exact prices and inference settings."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.model_capabilities import catalog_model, model_capabilities, reasoning_effort
from webot.runtime_settings import get_runtime_settings, save_runtime_settings, ContextSettings, resolve_context_window


class ModelCapabilityTests(unittest.TestCase):
    def test_native_profile_overrides_catalog(self):
        caps = model_capabilities("gpt-5", "openai", profile={"image_inputs": False, "max_input_tokens": 12345})
        self.assertFalse(caps["image_inputs"])
        self.assertEqual(caps["max_input_tokens"], 12345)
        self.assertEqual(caps["source"], "langchain")

    def test_exact_vendor_prefix_lookup(self):
        self.assertEqual(catalog_model("openai/gpt-4o-mini")["id"], "gpt-4o-mini")
        self.assertEqual(catalog_model("gpt-4o-mini", "custom")["id"], "gpt-4o-mini")
        self.assertEqual(catalog_model("gpt-4o-mini-made-up"), {})

    def test_unknown_model_has_no_claimed_features(self):
        caps = model_capabilities("my-private-model", "openai")
        self.assertEqual(caps["source"], "unknown")
        self.assertEqual(caps["reasoning_effort_levels"], [])
        self.assertIsNone(reasoning_effort("my-private-model", "openai", "high"))

    def test_reasoning_adapter_only_accepts_known_levels(self):
        with patch('common.model_capabilities.model_capabilities', return_value={"reasoning_effort_levels": ["low", "high"]}):
            self.assertEqual(reasoning_effort("test", "openai", "high"), "high")
            self.assertIsNone(reasoning_effort("test", "openai", "max"))

    def test_manual_capacity_wins_and_zero_follows_catalog(self):
        with patch('webot.context_limits.infer_model_context_window', return_value=123456):
            self.assertEqual(resolve_context_window(ContextSettings(), "test"), 123456)
            self.assertEqual(resolve_context_window(ContextSettings(context_window_tokens=500000), "test"), 500000)

    def test_user_inference_defaults_and_session_override(self):
        with tempfile.TemporaryDirectory() as tmp, patch('webot.runtime_settings.USER_FILES_DIR', Path(tmp)):
            save_runtime_settings('alice', settings={'inference': {'reasoning_effort': 'low'}})
            save_runtime_settings('alice', session_id='s', settings={'inference': {'reasoning_effort': 'high'}})
            self.assertEqual(get_runtime_settings('alice').inference.reasoning_effort, 'low')
            self.assertEqual(get_runtime_settings('alice', 's').inference.reasoning_effort, 'high')
            self.assertEqual(get_runtime_settings('bob').inference.reasoning_effort, '')

    def test_price_lookup_does_not_match_model_family_prefix(self):
        from webot.cost_tracker import CostEntry
        main = CostEntry('gpt-4o', input_tokens=1000000).calculate_cost()
        small = CostEntry('gpt-4o-mini', input_tokens=1000000).calculate_cost()
        self.assertLess(small, main)
        unknown = CostEntry('unknown-model', input_tokens=1000000)
        self.assertEqual(unknown.calculate_cost(), 0)
        self.assertEqual(unknown.pricing_status, 'unavailable')

    def test_vision_override_wins(self):
        from webot.message_builder import _is_vision_model
        with patch.dict('os.environ', {'LLM_MODEL': 'gpt-4o', 'LLM_VISION_SUPPORT': 'false'}):
            self.assertFalse(_is_vision_model())
        with patch.dict('os.environ', {'LLM_MODEL': 'unknown', 'LLM_VISION_SUPPORT': 'true'}):
            self.assertTrue(_is_vision_model())

    def test_reasoning_effort_reaches_native_payload(self):
        from common.llm_factory import create_chat_model
        from langchain_core.messages import HumanMessage
        with patch('common.model_capabilities.reasoning_effort', return_value='high'):
            model = create_chat_model(model='gpt-5', provider='openai', api_key='test', base_url='https://api.openai.com/v1', reasoning_effort='high')
        payload = model._get_request_payload([HumanMessage('test')])
        self.assertEqual(payload['reasoning']['effort'], 'high')
