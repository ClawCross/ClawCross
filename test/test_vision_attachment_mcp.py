import asyncio
import base64
import json
import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


_PNG_1X1 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class VisionAttachmentMcpTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory()
        self.workspace_root = Path(self.tmpdir.name)

        import webot.workspace as workspace_mod

        self.workspace_mod = workspace_mod
        self.original_workspace_dir = workspace_mod.WORKSPACE_DIR
        self.original_vision_support = os.environ.get("LLM_VISION_SUPPORT")
        workspace_mod.WORKSPACE_DIR = self.workspace_root
        os.environ["LLM_VISION_SUPPORT"] = "true"

    def tearDown(self):
        self.workspace_mod.WORKSPACE_DIR = self.original_workspace_dir
        if self.original_vision_support is None:
            os.environ.pop("LLM_VISION_SUPPORT", None)
        else:
            os.environ["LLM_VISION_SUPPORT"] = self.original_vision_support
        self.tmpdir.cleanup()

    def _write_test_image(self) -> Path:
        user_root = self.workspace_root / "users" / "alice"
        user_root.mkdir(parents=True, exist_ok=True)
        image_path = user_root / "pixel.png"
        image_path.write_bytes(base64.b64decode(_PNG_1X1))
        return image_path

    def test_attach_image_to_context_returns_lightweight_attachment_reference(self):
        self._write_test_image()

        from mcp_servers.vision import ATTACHMENT_MARKER, attach_image_to_context
        from mcp.types import CallToolResult, ImageContent, TextContent

        result = asyncio.run(
            attach_image_to_context(
                username="alice",
                session_id="default",
                filename="pixel.png",
                prompt="describe it",
            )
        )
        self.assertIsInstance(result, CallToolResult)
        self.assertFalse(result.isError)
        self.assertEqual(len(result.content), 2)
        self.assertIsInstance(result.content[0], TextContent)
        self.assertIsInstance(result.content[1], ImageContent)
        payload = json.loads(result.content[0].text)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["type"], ATTACHMENT_MARKER)
        self.assertEqual(payload["prompt"], "describe it")
        self.assertEqual(payload["attachments"][0]["mime_type"], "image/png")
        self.assertEqual(payload["attachments"][0]["name"], "pixel.png")
        self.assertIn(str(self.workspace_root / "users" / "alice"), payload["attachments"][0]["path"])
        self.assertNotIn("base64", result.content[0].text)
        self.assertEqual(result.content[1].mimeType, "image/png")
        self.assertGreater(len(result.content[1].data), 20)

    def test_attach_image_to_context_allows_absolute_path_outside_workspace(self):
        outside_path = Path(self.tmpdir.name) / "outside.png"
        outside_path.write_bytes(base64.b64decode(_PNG_1X1))

        from mcp_servers.vision import attach_image_to_context
        from mcp.types import CallToolResult, ImageContent

        result = asyncio.run(
            attach_image_to_context(
                username="alice",
                session_id="default",
                filename=str(outside_path),
            )
        )

        self.assertIsInstance(result, CallToolResult)
        self.assertFalse(result.isError)
        self.assertIsInstance(result.content[1], ImageContent)
        self.assertEqual(result.content[1].mimeType, "image/png")

    def test_agent_preserves_direct_mcp_image_tool_content(self):
        from core.agent import TeamAgent

        content = [
            {"type": "text", "text": "metadata"},
            {"type": "image", "base64": _PNG_1X1, "mime_type": "image/png"},
        ]

        self.assertTrue(TeamAgent._tool_message_content_has_image(content))
        self.assertFalse(TeamAgent._tool_message_content_has_image([{"type": "text", "text": "metadata"}]))

    def test_fastmcp_call_tool_serializes_image_content(self):
        self._write_test_image()

        from mcp.types import CallToolResult, ImageContent, TextContent
        from mcp_servers.vision import mcp

        result = asyncio.run(
            mcp.call_tool(
                "attach_image_to_context",
                {
                    "username": "alice",
                    "session_id": "default",
                    "filename": "pixel.png",
                    "prompt": "describe it",
                },
            )
        )

        self.assertIsInstance(result, CallToolResult)
        self.assertFalse(result.isError)
        self.assertEqual(len(result.content), 2)
        self.assertIsInstance(result.content[0], TextContent)
        self.assertIsInstance(result.content[1], ImageContent)
        self.assertEqual(result.content[1].mimeType, "image/png")
        self.assertGreater(len(result.content[1].data), 20)


if __name__ == "__main__":
    unittest.main()
