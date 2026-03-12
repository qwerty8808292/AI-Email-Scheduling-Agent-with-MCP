import importlib
import json
import sys
import types
from unittest import mock


def import_with_stubs(module_name, stubs):
    sys.modules.pop(module_name, None)
    with mock.patch.dict(sys.modules, stubs, clear=False):
        return importlib.import_module(module_name)


def build_agent_stubs():
    google_mod = types.ModuleType("google")
    google_mod.__path__ = []
    genai_mod = types.ModuleType("google.genai")
    genai_types_mod = types.ModuleType("google.genai.types")

    class DummyModels:
        def generate_content(self, *args, **kwargs):
            raise AssertionError("generate_content should be mocked in tests")

    class DummyClient:
        def __init__(self, *args, **kwargs):
            self.models = DummyModels()

    class GenerateContentConfig:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    genai_mod.Client = DummyClient
    genai_mod.types = genai_types_mod
    genai_types_mod.GenerateContentConfig = GenerateContentConfig
    google_mod.genai = genai_mod

    dotenv_mod = types.ModuleType("dotenv")
    dotenv_mod.load_dotenv = lambda *args, **kwargs: None

    stubs = {
        "google": google_mod,
        "google.genai": genai_mod,
        "google.genai.types": genai_types_mod,
        "dotenv": dotenv_mod,
    }

    mcp_mod = types.ModuleType("mcp")
    mcp_mod.__path__ = []

    class DummyClientSession:
        pass

    class DummyStdioServerParameters:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    mcp_mod.ClientSession = DummyClientSession
    mcp_mod.StdioServerParameters = DummyStdioServerParameters

    mcp_client_mod = types.ModuleType("mcp.client")
    mcp_client_mod.__path__ = []
    mcp_stdio_mod = types.ModuleType("mcp.client.stdio")
    mcp_stdio_mod.stdio_client = object()

    stubs.update(
        {
            "mcp": mcp_mod,
            "mcp.client": mcp_client_mod,
            "mcp.client.stdio": mcp_stdio_mod,
        }
    )
    return stubs


def import_agent_with_stubs():
    return import_with_stubs("agent", build_agent_stubs())


def build_calendar_mcp_stubs():
    fastmcp_mod = types.ModuleType("fastmcp")

    class DummyFastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self):
            def decorator(func):
                return func

            return decorator

        def run(self):
            return None

    fastmcp_mod.FastMCP = DummyFastMCP
    return {"fastmcp": fastmcp_mod}


def import_calendar_mcp_with_stubs():
    return import_with_stubs("calendar_mcp", build_calendar_mcp_stubs())


def make_llm_response(data):
    return types.SimpleNamespace(text=json.dumps(data, ensure_ascii=False))


class FakeToolResult:
    def __init__(self, text=""):
        self.content = [types.SimpleNamespace(text=text)] if text else []


class FakeMcpSession:
    def __init__(self, calendar_events=None):
        self.calendar_events = calendar_events or []
        self.calls = []

    async def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        if name == "get_calendar_events":
            return FakeToolResult(json.dumps(self.calendar_events, ensure_ascii=False))
        return FakeToolResult("ok")
