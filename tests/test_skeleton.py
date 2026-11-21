from lookat import config
from lookat.state import State


def test_config_spots():
    assert config.SPOTS == ["left", "middle", "right"]


def test_state_defaults():
    s = State()
    assert s.mode == "ROOM" and s.spot is None
