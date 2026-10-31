"""LLM profile resolution, tool calling and the fenced-script fallback."""
import json

import pytest

from app.make.llm import (
    LLMConfigError,
    build_user_content,
    clear_tool_cache,
    complete,
    describe,
    extract_fenced_python,
    get_client,
    list_models,
    system_prompt,
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch):
    clear_tool_cache()
    monkeypatch.setattr("app.make.llm.load_dotenv", lambda *args, **kwargs: None)
    for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL",
                 "ALT_LLM_BASE_URL", "ALT_LLM_API_KEY", "ALT_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_extracts_the_last_python_fence():
    text = "```json\n{\"a\": 1}\n```\n```python\nprint(1)\n```\n```\nprint(2)\n```"
    assert extract_fenced_python(text) == "print(2)"
    assert extract_fenced_python("no code here") is None
    assert extract_fenced_python("```py\nx = 1\n```") == "x = 1"


def test_system_prompt_states_the_contract():
    prompt = system_prompt()
    assert "mm()" in prompt
    assert "OVERRIDES" in prompt
    assert "PARAMS" in prompt
    assert "{component_catalog}" not in prompt
    assert "(none)" in prompt


def test_vision_off_omits_image_bytes():
    images = [{"mime": "image/png", "b64": "aaaa-image-bytes"}]
    omitted = build_user_content("hello", images, vision=False)
    assert "aaaa-image-bytes" not in omitted
    assert "image omitted" in omitted
    attached = build_user_content("hello", images, vision=True)
    assert attached[1]["image_url"]["url"] == "data:image/png;base64,aaaa-image-bytes"


def test_get_client_keeps_the_key_out_of_the_description(monkeypatch):
    secret = "sk-testsecretvalue999"
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", secret)
    seen = {}

    class FakeOpenAI:
        def __init__(self, *, base_url, api_key, max_retries=0, timeout=None):
            seen["base_url"] = base_url
            seen["api_key"] = api_key

    monkeypatch.setattr("app.make.llm.OpenAI", FakeOpenAI)
    _client, model, caps = get_client("muse")
    assert model == "muse-spark-1.3"
    assert seen["api_key"] == secret
    blob = json.dumps(caps)
    assert secret not in blob
    assert "llm.example.test" not in blob
    assert caps["ready"] is True
    listed = json.dumps(list_models())
    assert secret not in listed
    assert "llm.example.test" not in listed


def test_default_profile_model_override_does_not_change_alt(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-testsecretvalue999")
    monkeypatch.setenv("LLM_MODEL", "muse-spark-1.3-contributor")
    monkeypatch.setenv("ALT_LLM_BASE_URL", "https://alt.example.test/v1")
    monkeypatch.setenv("ALT_LLM_API_KEY", "sk-altsecretvalue99999")
    monkeypatch.setenv("ALT_LLM_MODEL", "alt-model-1")
    monkeypatch.setattr("app.make.llm.OpenAI", lambda **kwargs: object())
    _client, model, _caps = get_client("muse")
    assert model == "muse-spark-1.3-contributor"
    _client, alt_model, _caps = get_client("alt")
    assert alt_model == "alt-model-1"


def test_missing_key_names_the_variable_only(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    with pytest.raises(LLMConfigError) as caught:
        get_client("muse")
    message = str(caught.value)
    assert "LLM_API_KEY" in message
    assert "sk-" not in message
    assert "llm.example.test" not in message
    info = describe("muse")
    assert info["ready"] is False
    assert "LLM_API_KEY" in info["missing"]


class _Usage:
    prompt_tokens = 2
    completion_tokens = 3
    total_tokens = 5


class _Function:
    def __init__(self, arguments):
        self.name = "submit_blender_script"
        self.arguments = arguments


class _ToolCall:
    def __init__(self, arguments):
        self.function = _Function(arguments)


class _Message:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Response:
    def __init__(self, message):
        self.choices = [type("Choice", (), {"message": message})()]
        self.usage = _Usage()


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.handler(kwargs)


def test_tool_call_returns_the_script():
    payload = json.dumps({"script": "print(1)", "summary": "a box"})
    client = FakeClient(lambda kwargs: _Response(_Message("", [_ToolCall(payload)])))
    result = complete([{"role": "user", "content": "build"}], profile="muse", client=client)
    assert result["via"] == "tool"
    assert result["script"] == "print(1)"
    assert result["summary"] == "a box"
    assert set(result["event"]) == {"profile", "model", "latency_s", "tokens", "via", "attempt", "tools_attempted"}
    assert result["event"]["via"] == "tool"
    assert "base_url" not in result["event"]
    assert len(client.calls) == 1
    assert client.calls[0]["tools"][0]["function"]["name"] == "submit_blender_script"


def test_unsupported_tools_fall_back_to_a_fence_and_stay_off():
    def handler(kwargs):
        if kwargs.get("tools"):
            error = RuntimeError("tools unsupported by this model")
            error.status_code = 400
            raise error
        return _Response(_Message("notes\n```python\nprint('from-fence')\n```\n"))

    client = FakeClient(handler)
    result = complete([{"role": "user", "content": "build"}], profile="muse", client=client)
    assert result["via"] == "fence"
    assert result["script"] == "print('from-fence')"
    assert result["event"]["tokens"]["total"] == 5
    again = FakeClient(handler)
    second = complete([{"role": "user", "content": "build"}], profile="muse", client=again)
    assert second["via"] == "fence"
    assert "tools" not in again.calls[0]


def test_missing_tool_call_uses_the_fence():
    def handler(kwargs):
        if kwargs.get("tools"):
            return _Response(_Message("I forgot the tool", tool_calls=[]))
        return _Response(_Message("```python\nprint(2)\n```"))

    client = FakeClient(handler)
    result = complete([{"role": "user", "content": "build"}], profile="muse", client=client)
    assert result["via"] == "fence"
    assert result["script"] == "print(2)"
    assert len(client.calls) == 2
    assert result["event"]["tools_attempted"] is True


def test_fence_stays_the_default_after_the_memory_cache_is_cleared():
    """A miss is remembered for the profile, so the next process does not spend a tool round trip."""
    from app.make import llm

    def handler(kwargs):
        if kwargs.get("tools"):
            error = RuntimeError("tools unsupported")
            error.status_code = 400
            raise error
        return _Response(_Message("```python\nprint(1)\n```"))

    complete([{"role": "user", "content": "build"}], profile="muse", client=FakeClient(handler))
    llm._TOOLS_OFF.clear()
    again = FakeClient(handler)
    result = complete([{"role": "user", "content": "build"}], profile="muse", client=again)
    assert result["via"] == "fence"
    assert result["event"]["tools_attempted"] is False
    assert "tools" not in again.calls[0]
