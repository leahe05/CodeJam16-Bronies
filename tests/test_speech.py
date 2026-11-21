"""What the voice says: Announcer rules alone, then through Live with fake gaze + eyebrows."""
from lookat import config
from lookat.devices import Room, SimPlug
from lookat.live import Live
from lookat.speech import NURSE_CALLED, RAISE, Announcer, speakable


def screen(kind="idle", title="You're all set.", left="Room Controls", middle="Talk to AI", right=None, **kw):
    s = {"screen": kind, "title": title, "badge": None, "page": None,
         "left": {"label": left} if left else None, "middle": {"label": middle} if middle else None,
         "right": {"label": right} if right else None}
    s.update(kw)
    return s


def announcer(t=None):
    said = []
    return Announcer(said.append, clock=(lambda: t[0]) if t else (lambda: 0.0)), said


def test_lock_says_label_and_raise_once():
    a, said = announcer()
    idle = screen()
    a.update(idle, {"spot": "left", "state": "dwell"}, None)
    assert said == []                                   # the soft glow is silent
    for _ in range(5):
        a.update(idle, {"spot": "left", "state": "asking"}, None)
    assert said == [f"Room Controls. {RAISE}"]          # said once per lock, not every frame


def test_relocking_the_same_card_is_not_repeated_right_away():
    t = [0.0]
    a, said = announcer(t)
    idle = screen()
    a.update(idle, {"spot": "left", "state": "asking"}, None)
    a.update(idle, None, None)
    t[0] = 3.0
    a.update(idle, {"spot": "left", "state": "asking"}, None)     # gaze flicker: quiet
    assert len(said) == 1
    a.update(idle, None, None)
    t[0] = 12.0
    a.update(idle, {"spot": "left", "state": "asking"}, None)     # much later: said again
    assert len(said) == 2


def test_choice_says_label_and_new_screen():
    a, said = announcer()
    a.update(screen(), None, None)
    room = screen("room", "What would you like to adjust?", "Adjust the lights", "Return to main menu",
                  "Adjust the temperature")
    a.update(room, {"spot": "left", "state": "chosen"}, "Room Controls")
    assert said == ["Room Controls. What would you like to adjust?"]


def test_live_numbers_in_a_title_are_not_reread():
    a, said = announcer()
    temp = screen("temperature", "The room is 24.5 °C", "Cooler", "Back", "Warmer")
    a.update(temp, None, None)
    a.update(screen("temperature", "The room is 24.3 °C", "Cooler", "Back", "Warmer"), None, None)
    a.update(screen("temperature", "The room is 24.1 °C", "Cooler", "Back", "Warmer"), None, "Cooler")
    assert said == ["Cooler."]


def test_nurse_question_and_call_nurse():
    a, said = announcer()
    a.update(screen(), None, None)
    a.update(screen("nurse", "Did you sleep okay?", "Yes", None, "No"), None, None)
    a.update(screen("nurse", "Did you sleep okay?", "Yes", None, "No"), None, None, [("call_nurse",)])
    assert said == ["Your nurse asks: Did you sleep okay?", NURSE_CALLED]


def test_back_home_by_timeout_is_silent():
    a, said = announcer()
    a.update(screen("room", "What would you like to adjust?", "Adjust the lights", "Return to main menu"), None, None)
    a.update(screen(), None, None)
    assert said == []


def test_speakable_reads_degrees():
    assert speakable("The room is 24.5 °C\nnow") == "The room is 24.5 degrees now"


def test_live_speaks_while_choosing_the_lamp():
    live = Live(Room(lamp_ip="", fan_ip="", plug_factory=SimPlug), now=lambda: 0.0)
    said = []
    live.announcer = Announcer(said.append)
    t = 0.0

    def choose(spot):
        nonlocal t
        for _ in range(int((config.DWELL_S + 0.2) / 0.1)):
            live.frame(round(t, 3), spot)
            t += 0.1
        live.frame(round(t, 3), spot, ["confirm"])
        t += 0.1
        for _ in range(int((config.COOLDOWN_S + 0.3) / 0.1)):
            live.frame(round(t, 3), None)
            t += 0.1

    choose("left")      # Room Controls
    choose("left")      # Adjust the lights
    choose("left")      # Lamp on
    assert said == [f"Room Controls. {RAISE}", "Room Controls. What would you like to adjust?",
                    f"Adjust the lights. {RAISE}", "Adjust the lights. The lamp is OFF",
                    f"Lamp on. {RAISE}", "Lamp on."]
    assert live.room.state()["lamp_on"]
