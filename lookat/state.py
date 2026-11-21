"""Shared state between the camera loop (main thread) and the worker threads.

Read or change State only while holding state.lock. Workers get jobs through the queues.
"""
import json
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

LOG_PATH = os.path.join("logs", "events.jsonl")
_log_lock = threading.Lock()


@dataclass
class State:
    face: bool = False
    spot: Optional[str] = None
    confidence: float = 0.0
    machine_state: str = "IDLE"
    mode: str = "ROOM"                 # "ROOM" or "QUESTION"
    question: Optional[dict] = None    # spoken, left, middle, right, asked_at
    answers: list = field(default_factory=list)  # question, answer, seconds, time
    alerts: list = field(default_factory=list)
    lamp_on: bool = False
    metrics: dict = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)


state = State()

voice_q: "queue.Queue[str]" = queue.Queue()      # text to speak
lamp_q: "queue.Queue[str]" = queue.Queue()       # "toggle", "on", "off", "flash"
events_q: "queue.Queue[dict]" = queue.Queue()    # events for the nurse page
page_inbox: "queue.Queue[dict]" = queue.Queue()  # questions / cancel from the nurse


def log_event(type: str, **details) -> None:
    """Append one JSON line to logs/events.jsonl."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    line = json.dumps({"time": time.time(), "type": type, **details})
    with _log_lock:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
