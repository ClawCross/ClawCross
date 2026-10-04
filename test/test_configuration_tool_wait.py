"""Forms finish the original tool call, without credentials or inbox notifications."""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from channels import setup_requests as channels
from common.env_settings import read_env_all
from ops import configuration_requests as setup
from webot.engine.agent import DirectToolNode
from webot.mcp import notifier
from webot import runtime_store

PRIVATE_TOKEN = '123456789:' + 'A' * 30


class ConfigurationToolWaitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        config = self.root / 'config'; config.mkdir()
        self.env = config / '.env'
        self.inbox = self.root / 'runtime.db'
        for obj, name, value in [
            (setup, 'DB_PATH', config / 'configuration-setup.sqlite3'), (setup, 'ENV_FILE', self.env),
            (channels, 'DB_PATH', config / 'channel-setup.sqlite3'), (channels, 'ENV_FILE', self.env),
            (channels, 'PID_DIR', self.root / 'run'), (runtime_store, 'DEFAULT_DB_PATH', self.inbox),
        ]:
            p = patch.object(obj, name, value); p.start(); self.addCleanup(p.stop)

    async def pending(self):
        async def find():
            while True:
                requests = setup.list_requests('alice', 'one')
                if requests: return requests[0]
                await asyncio.sleep(.02)
        return await asyncio.wait_for(find(), 15)

    def node(self, channel=False):
        function = notifier.request_channel_setup if channel else notifier.request_configuration
        tool = StructuredTool.from_function(coroutine=function, name=function.__name__, description='Private form')
        call = {'name': tool.name, 'args': {'username':'alice', 'session_id':'one',
                **({'channel':'telegram'} if channel else {'topic':'model'})},
                'id': 'original-call', 'type': 'tool_call'}
        return DirectToolNode([tool]), {'messages':[AIMessage(content='', tool_calls=[call])]}

    async def test_save_and_cancel_finish_original_call_without_secret_or_inbox(self):
        for channel in (False, True):
            for cancel in (False, True):
                with self.subTest(channel=channel, cancel=cancel):
                    node, state = self.node(channel)
                    task = asyncio.create_task(node.ainvoke(state, {}))
                    self.addAsyncCleanup(self.stop_task, task)
                    request = await self.pending()
                    self.assertFalse(task.done(), 'No pending tool result may reach the Agent')
                    values = {} if cancel else ({'token':PRIVATE_TOKEN} if channel else {'LLM_API_KEY':'PRIVATE_KEY'})
                    setup.submit('alice', request['id'], values, cancel=cancel)
                    message = (await asyncio.wait_for(task, 3))['messages'][0]
                    self.assertEqual(message.tool_call_id, 'original-call')
                    result = json.loads(message.content)
                    self.assertEqual(result['status'], 'cancelled' if cancel else 'completed')
                    self.assertNotIn('PRIVATE', message.content)
                    self.assertNotIn(PRIVATE_TOKEN, message.content)
                    self.assertNotIn('values', result)
                    self.assertNotIn('draft', result)
                    self.assertFalse(self.inbox.exists())
                    self.assertEqual(setup.list_requests('alice', 'one'), [])
                    if not cancel:
                        raw = read_env_all(str(self.env))
                        self.assertIn(PRIVATE_TOKEN if channel else 'PRIVATE_KEY', json.dumps(raw))

    async def test_timeout_returns_expired_and_late_submission_is_rejected(self):
        for channel in (False, True):
            with self.subTest(channel=channel), patch.dict(os.environ, {'CLAWCROSS_FORM_WAIT_SECONDS':'0.03'}):
                node, state = self.node(channel)
                message = (await node.ainvoke(state, {}))['messages'][0]
                result = json.loads(message.content)
                self.assertEqual(result['status'], 'expired')
                self.assertEqual(setup.status('alice', result['id'])['status'], 'expired')
                with self.assertRaises(ValueError): setup.submit('alice', result['id'], {})
                self.assertFalse(self.env.exists())
                self.assertFalse(self.inbox.exists())

    async def test_stopping_tool_closes_form_and_does_not_save(self):
        for channel in (False, True):
            with self.subTest(channel=channel):
                node, state = self.node(channel)
                task = asyncio.create_task(node.ainvoke(state, {}))
                request = await self.pending()
                task.cancel()
                with self.assertRaises(asyncio.CancelledError): await task
                self.assertEqual(setup.status('alice', request['id'])['status'], 'cancelled')
                self.assertFalse(self.env.exists())
                self.assertFalse(self.inbox.exists())

    async def test_replacement_returns_cancelled_and_expiry_cannot_overwrite_saved_result(self):
        for topic in ('model', 'channel:telegram'):
            with self.subTest(topic=topic):
                old = setup.create('alice', 'one', topic)
                task = asyncio.create_task(setup.wait_for_result('alice', old, poll_interval=.01))
                self.addAsyncCleanup(self.stop_task, task)
                new = setup.create('alice', 'one', topic)
                self.assertEqual((await task)['status'], 'cancelled')
                values = {'LLM_MODEL':'new'} if topic == 'model' else {'token':PRIVATE_TOKEN}
                setup.submit('alice', new['id'], values)
                self.assertEqual(setup.close_pending('alice', new['id'], 'expired')['status'], 'completed')
                self.assertEqual(setup.list_requests('bob', include_finished=True), [])
                self.assertEqual(setup.list_requests('alice'), [])
                finished = setup.list_requests('alice', include_finished=True)
                self.assertTrue(any(row['id'] == new['id'] for row in finished))
                self.assertNotIn(PRIVATE_TOKEN, json.dumps(finished))

    async def test_real_mcp_process_waits_for_backend_save_or_cancel(self):
        script = Path(__file__).resolve().parents[1] / 'src/backend/webot/mcp/notifier.py'
        client = MultiServerMCPClient({'notifier': {
            'command':sys.executable, 'args':[str(script)], 'transport':'stdio',
            'env':{'CLAWCROSS_HOME':str(self.root), 'CLAWCROSS_FORM_WAIT_SECONDS':'30'},
        }})
        tools = await client.get_tools()
        node = DirectToolNode(tools)
        for channel, cancel in ((False, True), (True, False), (True, None)):
            with self.subTest(channel=channel, cancel=cancel):
                name = 'request_channel_setup' if channel else 'request_configuration'
                call = {'name':name, 'args':{'username':'alice','session_id':'one',
                        **({'channel':'telegram'} if channel else {'topic':'model'})},
                        'id':'mcp-original-call','type':'tool_call'}
                task = asyncio.create_task(node.ainvoke({'messages':[AIMessage(content='',tool_calls=[call])]}, {}))
                self.addAsyncCleanup(self.stop_task, task)
                request = await self.pending()
                self.assertFalse(task.done())
                if cancel is None:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
                    async def closed():
                        while setup.status('alice', request['id'])['status'] == 'pending':
                            await asyncio.sleep(.02)
                    await asyncio.wait_for(closed(), 3)
                    self.assertEqual(setup.status('alice', request['id'])['status'], 'cancelled')
                    continue
                setup.submit('alice', request['id'], {} if cancel else {'token':PRIVATE_TOKEN}, cancel=cancel)
                message = (await asyncio.wait_for(task, 15))['messages'][0]
                self.assertEqual(message.tool_call_id, 'mcp-original-call')
                text = message.content if isinstance(message.content, str) else ''.join(block.get('text', '') for block in message.content)
                self.assertEqual(json.loads(text)['status'], 'cancelled' if cancel else 'completed')
                self.assertNotIn(PRIVATE_TOKEN, text)
                self.assertFalse(self.inbox.exists())

    @staticmethod
    async def stop_task(task):
        if not task.done(): task.cancel()
        try: await task
        except asyncio.CancelledError: pass
