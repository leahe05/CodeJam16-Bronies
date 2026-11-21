"""Patient-agent report data and everyday care choices for the nurse demo."""

CARE_CHOICES = {
    "water": ("Water", "Would you like some water?", "Patient requests a glass of water."),
    "food": ("Food or snack", "Would you like something to eat?", "Patient requests food or a snack."),
    "clean_up": ("Help cleaning up", "Would you like help cleaning up?", "Patient requests help cleaning up."),
    "brief_change": ("Change brief or clothing", "Would you like help changing in private?", "Patient requests help changing in private."),
    "sit_up": ("Sit upright", "Would you like help sitting upright?", "Patient requests help sitting upright."),
    "reposition": ("Change position", "Would you like help changing position?", "Patient requests help changing position."),
    "move_blanket": ("Adjust blankets", "Would you like your blanket adjusted?", "Patient requests a blanket adjustment."),
    "add_blanket": ("More blankets", "Would you like another blanket?", "Patient requests another blanket."),
    "lighter_blanket": ("Lighter blanket", "Would you like a lighter blanket?", "Patient requests a lighter blanket."),
    "remove_blanket": ("Fewer blankets", "Would you like to remove a blanket?", "Patient requests removing a blanket."),
    "pillow": ("Adjust pillows", "Would you like your pillows adjusted?", "Patient requests a pillow adjustment."),
    "add_pillow": ("More pillows", "Would you like another pillow?", "Patient requests another pillow."),
    "remove_pillow": ("Fewer pillows", "Would you like a pillow removed?", "Patient requests removing a pillow."),
    "window": ("Open window", "Would opening the window a little help you sleep?", "Patient requests opening the window a little."),
    "close_window": ("Close window", "Would you like the window closed?", "Patient requests closing the window."),
    "open_blinds": ("Open blinds", "Would you like the blinds opened?", "Patient requests opening the blinds."),
    "close_blinds": ("Close blinds", "Would you like the blinds lowered?", "Patient requests closing the blinds."),
    "fan": ("Fan on low", "Would a fan on low help you sleep?", "Patient requests a fan on low."),
    "massage": ("Hand massage", "Would you like to ask the nurse about a gentle hand massage?", "Patient requests a gentle hand massage."),
}

MEASUREMENTS = ("heart_rate_bpm", "spo2_pct", "respiratory_rate_bpm", "systolic_mmhg", "diastolic_mmhg")

PATIENT_CONTEXT = {
    "avery": {
        "last_meal": {"name": "Evening meal", "time": "21:05", "minutes_before_start": 85},
        "habits": ("Prefers to sit upright for drinks.", "Usually keeps the blinds open during the day."),
        "vitals": ((70, 98, 15, 118, 73), (72, 98, 16, 120, 74), (73, 98, 16, 121, 75)),
        "window": "Closed", "blinds": "Open",
    },
    "morgan": {
        "last_meal": {"name": "Evening meal", "time": "20:10", "minutes_before_start": 140},
        "habits": ("Prefers privacy during personal care.", "Likes the blinds closed when resting."),
        "vitals": ((79, 97, 17, 123, 76), (77, 98, 16, 121, 75), (78, 98, 16, 122, 76)),
        "window": "Closed", "blinds": "Closed",
    },
}
