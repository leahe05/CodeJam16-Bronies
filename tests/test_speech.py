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


def announcer():
    """said: interrupting lines as-is, queued lines as "+<text>"."""
    said = []
    return Announcer(said.append, clock=lambda: 0.0, queue=lambda x: said.append("+" + x)), said


def look(a, scr, spot, state, t0, t1, step=0.1):
    t = t0
    while t < t1 - 1e-9:
        a.update(scr, {"spot": spot, "state": state} if spot else None, None, now=round(t, 3))
        t += step
    return t


def test_name_while_looking_then_cue_queued_after_it():
    a, said = announcer()
    idle = screen()
    look(a, idle, "left", "dwell", 0.0, 1.0)
    assert said == ["Room Controls."]                   # named during the dwell, once
    look(a, idle, "left", "asking", 1.0, 2.0)
    assert said == ["Room Controls.", "+" + RAISE]      # cue QUEUED: never cuts the name off


def test_looking_away_lets_the_name_finish():
    a, said = announcer()
    idle = screen()
    look(a, idle, "left", "dwell", 0.0, 0.5)
    a.update(idle, None, None, now=0.5)
    a.update(idle, {"spot": "left", "state": "dwell"}, None, now=0.6)   # gaze flicker back
    assert said == ["Room Controls."]                   # no stop, no repeat


def test_another_card_replaces_the_name():
    a, said = announcer()
    idle = screen()
    look(a, idle, "left", "dwell", 0.0, 0.5)
    look(a, idle, "middle", "dwell", 0.5, 1.0)
    assert said == ["Room Controls.", "Talk to AI."]    # say() interrupts


def test_a_quick_flicker_is_not_named():
    a, said = announcer()
    idle = screen()
    a.update(idle, {"spot": "left", "state": "dwell"}, None, now=0.0)
    a.update(idle, None, None, now=0.1)
    assert said == []


def test_lock_without_a_dwell_name_still_names_then_cues():
    a, said = announcer()
    a.update(screen(), {"spot": "left", "state": "asking"}, None, now=0.0)
    assert said == ["Room Controls.", "+" + RAISE]


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
    live.announcer = Announcer(said.append, queue=lambda x: said.append("+" + x))
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
    assert said == ["Room Controls.", "+" + RAISE, "Room Controls. What would you like to adjust?",
                    "Adjust the lights.", "+" + RAISE, "Adjust the lights. The lamp is OFF",
                    "Lamp on.", "+" + RAISE, "Lamp on."]
    assert live.room.state()["lamp_on"]
