import math

import numpy as np
import pytest

from lookat.vision import GAZE_FEATURES, iris_offset, matrix_to_angles


def test_identity_matrix_gives_zero_angles():
    assert matrix_to_angles(np.eye(4)) == pytest.approx((0.0, 0.0, 0.0))


def test_yaw_rotation():
    a = math.radians(30)
    # rotation about the y axis
    m = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    pitch, yaw, roll = matrix_to_angles(m)
    assert yaw == pytest.approx(30.0)
    assert pitch == pytest.approx(0.0, abs=1e-9) and roll == pytest.approx(0.0, abs=1e-9)


def test_iris_offset_centred_and_shifted():
    assert iris_offset((50, 10), (0, 10), (100, 10)) == pytest.approx((0.0, 0.0))
    assert iris_offset((75, 20), (0, 10), (100, 10)) == pytest.approx((0.25, 0.1))


def test_gaze_vector_layout():
    assert len(GAZE_FEATURES) == 15 and len(set(GAZE_FEATURES)) == 15
