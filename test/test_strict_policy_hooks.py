from types import SimpleNamespace
from unittest.mock import patch

import pytest

from webot import runtime_settings
from webot.policy import (ToolPolicyDecision, ToolPolicyHook,
                          WeBotToolPolicy, run_tool_policy_hooks)


@pytest.mark.parametrize('event', ['before', 'after', 'session_start', 'user_prompt_submit', 'pre_compact'])
def test_strict_policy_cannot_run_host_shell_or_write_custom_log(tmp_path, event):
    outside = tmp_path / 'outside.jsonl'
    policy = WeBotToolPolicy(hooks=(
        ToolPolicyHook(event=event, hook_type='shell_command', command='do-not-execute'),
        ToolPolicyHook(event=event, path=str(outside)),
    ))
    denied = ToolPolicyDecision(allowed=False, reason='hard deny')
    with patch.object(runtime_settings, 'get_runtime_settings', return_value=SimpleNamespace(
            approval=SimpleNamespace(sandbox_security='strict'))), \
            patch('webot.policy.subprocess.run') as execute:
        outcome = run_tool_policy_hooks(policy, event=event, user_id='alice', session_id='s',
                                        tool_name='run_command', args={'command': 'safe'}, decision=denied)
        execute.assert_not_called()
    assert not outside.exists()
    assert outcome.decision == denied
    assert outcome.args == {'command': 'safe'}
