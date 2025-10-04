import types

import comment_engine


def test_generate_comment_reuses_cached_client(monkeypatch):
    calls = []

    class DummyResponses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return types.SimpleNamespace(output_text="dummy-comment")

    class DummyClient:
        instances = 0

        def __init__(self, api_key):
            DummyClient.instances += 1
            self.api_key = api_key
            self.responses = DummyResponses()

    monkeypatch.setattr(comment_engine, "OpenAI", DummyClient)
    monkeypatch.setattr(comment_engine, "key", "test-key")
    monkeypatch.setattr(comment_engine, "_client", None)

    first = comment_engine.generate_comment("post", "system")
    second = comment_engine.generate_comment("post-2", "system-2")

    assert first == "dummy-comment"
    assert second == "dummy-comment"
    assert DummyClient.instances == 1
    assert len(calls) == 2


def test_generate_comment_without_api_key(monkeypatch, capsys):
    def fail_openai(*args, **kwargs):
        raise AssertionError("OpenAI should not be instantiated when key is missing")

    monkeypatch.setattr(comment_engine, "OpenAI", fail_openai)
    monkeypatch.setattr(comment_engine, "key", None)
    monkeypatch.setattr(comment_engine, "_client", None)

    result = comment_engine.generate_comment("post", "system")

    assert result == ""
    captured = capsys.readouterr()
    assert "OPENAI_API_KEY" in captured.out
