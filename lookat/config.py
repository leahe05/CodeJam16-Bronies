"""All LookAt constants. Change numbers here, nowhere else."""
import os
from typing import Optional


def saved_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """os.environ first; on Windows also the value `setx` saved for the user, so a
    terminal that was opened before setx still works. Values are never printed."""
    value = os.environ.get(name)
    if value or os.name != "nt":
        return value or default
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, name)[0] or default
    except OSError:
        return default


# Interaction timing (seconds)
DWELL_S = 1.0          # look at a spot this long -> it is highlighted and locked
CONFIRM_S = 0.3        # eyebrow raise held this long = choose
ASK_TIMEOUT_S = 3.0    # the raise must come within this time after the spot locks
COOLDOWN_S = 1.0       # pause after a choice (then the patient must look away before choosing again)
EMERGENCY_S = 2.0      # eyebrow held this long = Call Nurse, on any screen
MOUTH_CONFIRM_S = 0.7  # backup confirm: jaw open held this long
CHOSEN_FLASH_S = 0.5   # the chosen option flashes this long on the patient screen

# Patient screens (seconds)
SUGGESTION_FADE_S = 20  # an ignored AI suggestion fades back to idle
CHECKIN_TIMEOUT_S = 30  # an unanswered check-in closes as "ignored"
ROOM_TIMEOUT_S = 30     # room-control screens go back to idle with no choice
OVERLAY_S = 6           # "Your nurse has been called" stays this long
QUIET_S = 7200          # "Quiet for 2 hours"

# Time of day (demo clock, lookat/clock.py)
NIGHT_START = "22:00"
NIGHT_END = "07:00"

# Spots (the patient's left / center / right)
SPOTS = ["left", "middle", "right"]

# Camera and vision
CAMERA = (1280, 720)
MIN_FACE_PX = 250
MODEL_PATH = "models/face_landmarker.task"

# Network
PAGE_PORT = 8000
WIFI_NAME = os.environ.get("LOOKAT_WIFI", "lea")   # the plugs only work on this network
LAMP_IP = saved_env("LAMP_IP", "172.20.10.2")
FAN_IP = saved_env("FAN_IP", "")  # empty = simulated fan (setx FAN_IP "172.20.10.x")
