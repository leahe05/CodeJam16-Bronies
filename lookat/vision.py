"""Camera -> face features (owner P1).

FaceTracker.read() returns (frame, features). features is None when no usable face,
else a dict with face size, head angles, eye blendshapes, iris offsets, brow/jaw/blink
scores and a fixed-order numpy gaze_vector used by lookat.gaze.

Self-test: py -3.11 -m lookat.vision   (q quits)
"""
import math
import os
import sys
import time

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision

from lookat import config

EYE_BLENDSHAPES = [
    "eyeLookInLeft", "eyeLookInRight",
    "eyeLookOutLeft", "eyeLookOutRight",
    "eyeLookUpLeft", "eyeLookUpRight",
    "eyeLookDownLeft", "eyeLookDownRight",
]
OTHER_BLENDSHAPES = ["browInnerUp", "jawOpen", "eyeBlinkLeft", "eyeBlinkRight"]
IRIS_KEYS = ["iris_a_x", "iris_a_y", "iris_b_x", "iris_b_y"]
# Fixed order of gaze_vector. Never reorder: saved calibrations depend on it.
GAZE_FEATURES = ["pitch", "yaw", "roll"] + EYE_BLENDSHAPES + IRIS_KEYS

# (iris centre, eye corner 1, eye corner 2) landmark indices
EYES = [(468, 33, 133), (473, 362, 263)]

LOCK_RATIO = 0.6      # ignore faces smaller than this share of the locked face width
LOCK_RESET_S = 2.0    # forget the locked face after this long without it

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def matrix_to_angles(m) -> tuple:
    """4x4 (or 3x3) head transformation matrix -> (pitch, yaw, roll) in degrees."""
    r = np.asarray(m, dtype=float)[:3, :3]
    pitch = math.atan2(r[2, 1], r[2, 2])
    yaw = math.asin(max(-1.0, min(1.0, -r[2, 0])))
    roll = math.atan2(r[1, 0], r[0, 0])
    return math.degrees(pitch), math.degrees(yaw), math.degrees(roll)


def iris_offset(iris, corner1, corner2) -> tuple:
    """Iris centre relative to the middle of the eye corners, divided by eye width.

    Points are (x, y) in pixels. Returns (x, y); 0, 0 = iris centred.
    """
    iris, c1, c2 = (np.asarray(p, dtype=float) for p in (iris, corner1, corner2))
    width = float(np.linalg.norm(c2 - c1)) or 1.0
    off = (iris - (c1 + c2) / 2) / width
    return float(off[0]), float(off[1])


def _model_path() -> str:
    p = config.MODEL_PATH
    return p if os.path.isabs(p) or os.path.exists(p) else os.path.join(_REPO, p)


def _open_camera(index: int):
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    cap = cv2.VideoCapture(index, backend)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.CAMERA[0])
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.CAMERA[1])
    if not cap.isOpened():
        raise RuntimeError(f"Camera {index} did not open")
    return cap


class FaceTracker:
    def __init__(self, camera_index: int = 0):
        self.cap = _open_camera(camera_index)
        options = mp_vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_model_path()),
            running_mode=mp_vision.RunningMode.VIDEO,
            output_face_blendshapes=True,
            output_facial_transformation_matrixes=True,
            num_faces=2,
        )
        self.landmarker = mp_vision.FaceLandmarker.create_from_options(options)
        self._last_ts = -1
        self._locked_px = None     # width of the face we are tracking
        self._locked_seen = 0.0    # when we last saw it

    def read(self):
        """Grab one frame. Returns (frame, features) or (None, None) if the camera failed."""
        ok, frame = self.cap.read()
        if not ok:
            return None, None
        ts = int(time.monotonic() * 1000)
        ts = max(ts, self._last_ts + 1)   # MediaPipe needs strictly increasing timestamps
        self._last_ts = ts
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        result = self.landmarker.detect_for_video(
            mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), ts)
        return frame, self._features(result, frame.shape[1], frame.shape[0])

    def _features(self, result, w: int, h: int):
        if not result.face_landmarks:
            return None
        # pick the largest face
        widths = []
        for lms in result.face_landmarks:
            xs = [p.x for p in lms]
            widths.append((max(xs) - min(xs)) * w)
        i = int(np.argmax(widths))
        face_px = widths[i]

        now = time.monotonic()
        if self._locked_px is not None and now - self._locked_seen > LOCK_RESET_S:
            self._locked_px = None
        if self._locked_px is not None and face_px < LOCK_RATIO * self._locked_px:
            return None   # only a small (background) face is visible
        self._locked_px = face_px if self._locked_px is None else 0.9 * self._locked_px + 0.1 * face_px
        self._locked_seen = now

        lms = result.face_landmarks[i]
        scores = {b.category_name: b.score for b in result.face_blendshapes[i]}
        pitch, yaw, roll = matrix_to_angles(result.facial_transformation_matrixes[i])

        feats = {"face_px": face_px, "pitch": pitch, "yaw": yaw, "roll": roll}
        for name in EYE_BLENDSHAPES + OTHER_BLENDSHAPES:
            feats[name] = float(scores.get(name, 0.0))

        px = lambda k: (lms[k].x * w, lms[k].y * h)
        for (iris, c1, c2), (kx, ky) in zip(EYES, [IRIS_KEYS[0:2], IRIS_KEYS[2:4]]):
            feats[kx], feats[ky] = iris_offset(px(iris), px(c1), px(c2))

        feats["gaze_vector"] = np.array([feats[k] for k in GAZE_FEATURES], dtype=float)
        return feats

    def close(self) -> None:
        self.cap.release()
        self.landmarker.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _self_test(camera_index: int) -> None:
    tracker = FaceTracker(camera_index)
    frames, start, last = 0, time.monotonic(), time.monotonic()
    fps = 0.0
    try:
        while True:
            frame, f = tracker.read()
            if frame is None:
                print("FAIL: camera returned no frame")
                break
            frames += 1
            now = time.monotonic()
            fps = 0.9 * fps + 0.1 * (1.0 / max(now - last, 1e-6)) if frames > 1 else 0.0
            last = now

            cv2.putText(frame, f"fps {fps:4.1f}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
            if f is None:
                cv2.putText(frame, "NO FACE", (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 3)
            else:
                good = f["face_px"] >= config.MIN_FACE_PX
                color = (0, 200, 0) if good else (0, 165, 255)
                text = f"face {f['face_px']:.0f}px" + ("" if good else "  MOVE CLOSER")
                cv2.putText(frame, text, (20, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.2, color, 3)
                cv2.putText(frame, f"yaw {f['yaw']:+6.1f}  pitch {f['pitch']:+6.1f}  roll {f['roll']:+6.1f}",
                            (20, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
                cv2.putText(frame, f"brow {f['browInnerUp']:.2f}  jaw {f['jawOpen']:.2f}",
                            (20, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
            cv2.imshow("LookAt vision (operator)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        elapsed = time.monotonic() - start
        tracker.close()
        cv2.destroyAllWindows()
        avg = frames / elapsed if elapsed > 0 else 0.0
        print(f"average fps: {avg:.1f} over {frames} frames")
        print("PASS" if avg >= 15 else "FAIL: fps below 15")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="FaceTracker self-test")
    ap.add_argument("--camera", type=int, default=0)
    _self_test(ap.parse_args().camera)
