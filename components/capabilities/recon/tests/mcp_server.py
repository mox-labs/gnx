"""An MCP server for recon's tests: the SDK's own server, so the client meets a real one.

``python tests/mcp_server.py`` serves the fixture over stdio; ``FIXTURE`` is the same server
for a test to mount in process (``FIXTURE.streamable_http_app()``). Its tools and resources:

* ``search``: structured content, under the output schema its return type declares;
* ``listing``: JSON in its text content, with no output schema;
* ``wrapped``: an output schema of one string (``{"result": "<json>"}``), and the JSON again in
  its text, as servers that wrap their text answer do;
* ``broken``: a tool error; ``nothing``: an answer with no content; ``prose``: text that is
  not JSON;
* ``echo``: the object it is given, as structured content and as JSON in its text;
* ``whoami``: the server's process id, and the ``FIXTURE_TOKEN`` and ``HOME`` it was started
  with, as JSON text;
* ``fixture://papers``, and the template ``fixture://papers/{id}``: JSON text.

``python tests/mcp_server.py --replay FILE`` serves one tool, named and described as a
recording (``{"tool", "input_schema", "output_schema", "result"}``) says, answering every call
with the recorded result: a template's collector runs against what a real server returned.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import anyio
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.mcpserver import MCPServer
from mcp.server.stdio import stdio_server
from pydantic import BaseModel

FIXTURE = MCPServer("recon-fixture")


class Paper(BaseModel):
    id: str
    title: str


class Papers(BaseModel):
    papers: list[Paper]


@FIXTURE.tool()
def search(query: str, limit: int = 2) -> Papers:
    """Papers about ``query``, as structured content."""
    return Papers(papers=[Paper(id=f"p{i}", title=f"{query} {i}") for i in range(limit)])


@FIXTURE.tool(structured_output=False)
def listing(query: str) -> str:
    """Papers about ``query``, as JSON text."""
    return json.dumps({"items": [{"id": "a", "q": query}, {"id": "b", "q": query}]})


@FIXTURE.tool()
def wrapped(query: str) -> str:
    """JSON text, which the SDK also wraps as structured content ``{"result": ...}``."""
    return json.dumps([{"id": "w1", "q": query}])


@FIXTURE.tool(structured_output=False)
def broken(query: str) -> str:
    """Always fails."""
    raise ValueError(f"the index is down for {query}")


@FIXTURE.tool()
def nothing() -> types.CallToolResult:
    """Answers with no content at all."""
    return types.CallToolResult(content=[])


@FIXTURE.tool(structured_output=False)
def prose() -> str:
    """Answers in words."""
    return "Here are some papers you might like."


@FIXTURE.tool()
def echo(answer: dict[str, Any]) -> dict[str, Any]:
    """``answer`` as given, as structured content (and its JSON in the text): an answer the
    test writes."""
    return answer


@FIXTURE.tool(structured_output=False)
def whoami() -> str:
    """Who answers: the process, the directory it runs in, and what its environment holds."""
    return json.dumps(
        {
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "token": os.environ.get("FIXTURE_TOKEN"),
            "home": "HOME" in os.environ,
        }
    )


@FIXTURE.resource("fixture://papers")
def papers() -> str:
    return json.dumps({"items": [{"id": "r1"}, {"id": "r2"}]})


@FIXTURE.resource("fixture://papers/{id}")
def paper(id: str) -> str:  # noqa: A002 - the template's own name
    return json.dumps({"id": id, "title": f"paper {id}"})


def replay(recording: dict[str, Any]) -> Server[Any]:
    """A server with the one tool ``recording`` describes, answering with its result."""
    tool = types.Tool(
        name=recording["tool"],
        input_schema=recording["input_schema"],
        output_schema=recording.get("output_schema"),
    )
    result = types.CallToolResult.model_validate(recording["result"])

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[tool])

    async def call_tool(ctx: Any, params: Any) -> types.CallToolResult:
        return result

    return Server("recon-replay", on_list_tools=list_tools, on_call_tool=call_tool)


async def _serve(server: Server[Any]) -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    if sys.argv[1:2] == ["--replay"]:
        anyio.run(_serve, replay(json.loads(Path(sys.argv[2]).read_text())))
    else:
        FIXTURE.run("stdio")
