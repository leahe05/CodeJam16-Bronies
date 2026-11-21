"""All LookAt constants. Change numbers here, nowhere else."""
import os

# Interaction timing (seconds)
DWELL_S = 1.0          # look at a spot this long -> it is spoken and locked
CONFIRM_S = 0.3        # eyebrow raise held this long = confirm
ASK_TIMEOUT_S = 3.0    # confirm must come within this time after the spot is spoken
COOLDOWN_S = 2.0       # pause after an action
EMERGENCY_S = 2.0      # eyebrow held this long = emergency, in any mode
MENU_STEP_S = 1.5      # middle spot menu: time between items
MOUTH_CONFIRM_S = 0.7  # backup confirm: jaw open held this long
QUESTION_TIMEOUT_S = 30

# Spots and meanings
SPOTS = ["left", "middle", "right"]
ROOM_MEANING = {"left": "yes", "middle": "lamp", "right": "no"}
MENU_ITEMS = ["Help", "Water", "Pain"]

# Camera and vision
CAMERA = (1280, 720)
MIN_FACE_PX = 250
MODEL_PATH = "models/face_landmarker.task"

# Network
PAGE_PORT = 8000
LAMP_IP = os.environ.get("LAMP_IP", "172.20.10.2")
