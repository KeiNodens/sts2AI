"""Verify the packaged MCP entry point without starting or controlling a game."""
import asyncio
from datetime import timedelta
import os
from pathlib import Path
import sys
import unittest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class TestMCPStdio(unittest.TestCase):
    def test_initialize_and_list_tools_without_game(self):
        from mcp_bridge import server
        self.assertTrue(server._ADVISOR, "The packaged advisor must be available")
        async def check():
            params = StdioServerParameters(command=sys.executable, args=["-B", "-m", "mcp_bridge.server"], cwd=str(Path(__file__).resolve().parents[1]), env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            async with stdio_client(params) as streams:
                async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=20)) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    names = {t.name for t in result.tools}
                    self.assertIn("get_game_state", names)
                    self.assertIn("get_decision_pack", names)
                    self.assertIn("flush_observations", names)
        asyncio.run(asyncio.wait_for(check(), timeout=45))
