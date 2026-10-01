# Examples

Runnable examples for application APIs. Run them with the prepared runtime
Python (`~/.clawcross/venv/bin/python` on Linux/macOS), from the repository root.
Start ClawCross and configure a model before sending chat requests.

- `api/chat.py`: interactive or single-message OpenAI-compatible API client.
  Reads `INTERNAL_TOKEN` from the runtime environment; defaults to chat mode.
- `oasis/manual_topic_control.py`: create, publish to and conclude an OASIS topic.

```bash
~/.clawcross/venv/bin/python examples/api/chat.py --user alice
~/.clawcross/venv/bin/python examples/oasis/manual_topic_control.py --help
```

Checkpoint inspection is an operator tool in `tools/diagnostics/view_history.py`.
