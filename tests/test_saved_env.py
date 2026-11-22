"""A key saved with setx must reach the agent even in a terminal opened before setx."""
from lookat import config


def test_saved_key_is_copied_into_a_terminal_that_does_not_have_it(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "saved_env", lambda name, default=None: "saved-key")
    config.load_saved_env("ANTHROPIC_API_KEY")
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "saved-key"


def test_a_key_already_in_the_terminal_is_kept(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "terminal-key")
    monkeypatch.setattr(config, "saved_env", lambda name, default=None: "saved-key")
    config.load_saved_env("ANTHROPIC_API_KEY")
    import os
    assert os.environ["ANTHROPIC_API_KEY"] == "terminal-key"


def test_nothing_saved_leaves_the_key_unset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config, "saved_env", lambda name, default=None: None)
    config.load_saved_env("ANTHROPIC_API_KEY")
    import os
    assert "ANTHROPIC_API_KEY" not in os.environ
