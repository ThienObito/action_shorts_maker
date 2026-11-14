"""Run both modes through MCP and follow the background job to completion."""
import asyncio
import json
import sys
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    root = Path(__file__).resolve().parent
    params = StdioServerParameters(command=sys.executable, args=[str(root / 'mcp_server.py')], cwd=str(root))
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            response = await client.call_tool('process_youtube_links', {
                'urls': 'https://www.youtube.com/watch?v=mMxWG3pB7bI',
                'mode': 'both', 'max_clips': 1, 'background': True})
            if response.isError:
                raise RuntimeError(str(response.content))
            job = response.structuredContent['result']
            job_id = job['job_id']
            print('MCP_JOB', job_id, flush=True)
            last = None
            started = time.monotonic()
            while time.monotonic() - started < 1800:
                status_response = await client.call_tool('get_job_status', {'job_id': job_id})
                if status_response.isError:
                    raise RuntimeError(str(status_response.content))
                payload = status_response.structuredContent
                if payload is None:
                    payload = json.loads(status_response.content[0].text)
                status = payload.get('result', payload)['jobs'][0]
                state = (status['status'], status['percent'], status['message'])
                if state != last:
                    print('MCP_STATUS', *state, flush=True)
                    last = state
                if status['status'] == 'completed':
                    print('MCP_BOTH_COMPLETE', json.dumps(status['result'], ensure_ascii=True), flush=True)
                    return
                if status['status'] == 'failed':
                    raise RuntimeError(status['message'])
                await asyncio.sleep(4)
            raise TimeoutError('MCP job did not complete within 30 minutes')


if __name__ == '__main__':
    asyncio.run(main())
