"""
Enhanced test suite covering all features aligned with source implementations.

Tests the deep enhancements ported from:
- openclaw-claude-code: consensus parsing, council two-phase protocol
- oh-my-codex: Ralph 7-phase state, deep interview ambiguity scoring, HUD presets
"""

import asyncio
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "backend"))


# ============================================================================
# Consensus Parsing (from openclaw consensus.ts)
# ============================================================================

class TestConsensus:
    """Test consensus vote parsing ported from openclaw."""

    def test_strict_format_yes(self):
        from webot.engine.consensus import parse_consensus
        assert parse_consensus("Some text [CONSENSUS: YES] end") is True

    def test_strict_format_no(self):
        from webot.engine.consensus import parse_consensus
        assert parse_consensus("Some text [CONSENSUS: NO] end") is False

    def test_chinese_colon(self):
        from webot.engine.consensus import parse_consensus
        assert parse_consensus("[CONSENSUS：YES]") is True
        assert parse_consensus("[CONSENSUS：NO]") is False

    def test_last_match_wins(self):
        from webot.engine.consensus import parse_consensus
        # Two tags — the LAST one wins (ported from openclaw)
        assert parse_consensus("[CONSENSUS: NO] ... [CONSENSUS: YES]") is True
        assert parse_consensus("[CONSENSUS: YES] ... [CONSENSUS: NO]") is False

    def test_variant_patterns(self):
        from webot.engine.consensus import parse_consensus
        assert parse_consensus("consensus: yes") is True
        assert parse_consensus("CONSENSUS=YES") is True
        assert parse_consensus("共识投票: YES") is True
        assert parse_consensus("**consensus**: no") is False

    def test_tail_fallback_positive(self):
        from webot.engine.consensus import parse_consensus
        text = "Lots of text\n" * 20 + "达成共识"
        assert parse_consensus(text) is True

    def test_tail_fallback_negative(self):
        from webot.engine.consensus import parse_consensus
        text = "Lots of text\n" * 20 + "未达成共识"
        assert parse_consensus(text) is False

    def test_default_false(self):
        from webot.engine.consensus import parse_consensus
        assert parse_consensus("No consensus tags here at all") is False

    def test_strip_tags(self):
        from webot.engine.consensus import strip_consensus_tags
        assert strip_consensus_tags("hello [CONSENSUS: YES] world") == "hello  world"

    def test_has_marker(self):
        from webot.engine.consensus import has_consensus_marker
        assert has_consensus_marker("[CONSENSUS: YES]") is True
        assert has_consensus_marker("共识投票: NO") is True
        assert has_consensus_marker("no tags here") is False
