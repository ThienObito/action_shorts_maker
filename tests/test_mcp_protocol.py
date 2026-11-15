"""Exercise real stdio JSON-RPC initialization and tool invocation."""
import asyncio
import sys
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class ProtocolTests(unittest.TestCase):
    def test_stdio_initialize_list_and_call(self):
        root = Path(__file__).resolve().parents[1]

        async def exchange():
            params = StdioServerParameters(command=sys.executable,
                                           args=[str(root / 'mcp_server.py')], cwd=str(root))
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    tools = await client.list_tools()
                    names = {tool.name for tool in tools.tools}
                    self.assertEqual(names, {'process_youtube_links',
                                             'analyze_video_storyboard',
                                             'list_completed_projects', 'get_job_status',
                                             'produce_us_documentary', 'open_agent_studio'})
                    result = await client.call_tool('list_completed_projects', {})
                    self.assertFalse(result.isError)
                    invalid = await client.call_tool('process_youtube_links',
                                                     {'urls': [], 'mode': 'both', 'max_clips': 1})
                    self.assertTrue(invalid.isError)
                    status = await client.call_tool('get_job_status', {})
                    self.assertFalse(status.isError)
                    preflight = await client.call_tool('produce_us_documentary',
                                                       {'youtube_urls': ['https://youtu.be/example']})
                    self.assertFalse(preflight.isError)
                    self.assertIn('preflight_required', str(preflight.content))

        asyncio.run(exchange())
