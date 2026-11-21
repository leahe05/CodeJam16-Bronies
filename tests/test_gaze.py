import numpy as np

from lookat.gaze import GazeModel, self_check, separation_scores

SPOTS = ["left", "middle", "right"]


def clusters(n=90, dims=15, spread=1.0, seed=0):
    rng = np.random.default_rng(seed)
    centres = {s: rng.normal(0, 5, dims) for s in SPOTS}
    X = np.vstack([centres[s] + rng.normal(0, spread, (n, dims)) for s in SPOTS])
    y = np.repeat(SPOTS, n)
    return X, y, centres, rng


def feats(vec):
    return {"gaze_vector": vec}


def test_knn_over_95_percent_on_clusters():
    X, y, centres, rng = clusters()
    model = GazeModel(X, y)
    trials = [(s, centres[s] + rng.normal(0, 1.0, 15)) for s in SPOTS for _ in range(100)]
    correct = sum(model.classify(v)[0] == s for s, v in trials)
    assert correct / len(trials) > 0.95


def test_vote_gives_stable_spot_with_high_confidence():
    X, y, centres, rng = clusters()
    model = GazeModel(X, y)
    for _ in range(10):
        spot, conf = model.predict(feats(centres["right"] + rng.normal(0, 1.0, 15)))
    assert spot == "right" and conf >= 0.9


def test_vote_ignores_a_single_stray_frame():
    X, y, centres, _ = clusters()
    model = GazeModel(X, y)
    for _ in range(9):
        model.predict(feats(centres["left"]))
    spot, _ = model.predict(feats(centres["right"]))
    assert spot == "left"


def test_low_confidence_returns_none():
    X, y, centres, _ = clusters()
    model = GazeModel(X, y)
    for i in range(10):  # alternating spots -> no majority strong enough
        spot, conf = model.predict(feats(centres[SPOTS[i % 2]]))
    assert spot is None and conf < 0.7


def test_no_face_resets_history():
    X, y, centres, _ = clusters()
    model = GazeModel(X, y)
    for _ in range(10):
        model.predict(feats(centres["left"]))
    assert model.predict(None) == (None, 0.0)
    assert len(model.history) == 0


def test_separation_and_self_check():
    X, y, _, _ = clusters()
    scores = separation_scores(X, y)
    assert set(scores) == {"left-middle", "left-right", "middle-right"}
    assert all(s > 3 for s in scores.values())
    assert self_check(X, y) > 0.95


def test_round_check_learns_round_one_tests_round_two():
    from lookat.gaze import round_check
    X, y, centres, rng = clusters(n=60)
    rounds = np.tile(np.repeat([0, 1], 30), 3)
    assert round_check(X, y, rounds) > 0.95


def test_small_head_turn_does_not_beat_the_eyes():
    from lookat.gaze import MIN_STD
    # 15 features like vision.GAZE_FEATURES: head angles barely move during calibration,
    # the eyes (feature 3 = eyeLookInLeft) clearly separate the spots
    rng = np.random.default_rng(1)
    X, y = [], []
    for spot, eye in (("left", 0.0), ("middle", 0.4), ("right", 0.8)):
        for _ in range(60):
            v = np.zeros(15)
            v[:3] = rng.normal(0, 0.3, 3)            # head almost still (std 0.3°)
            v[3] = eye + rng.normal(0, 0.03)
            v[4:] = rng.normal(0, 0.01, 11)
            X.append(v)
            y.append(spot)
    model = GazeModel(np.array(X), np.array(y))
    probe = np.zeros(15)
    probe[1] = 3.0                                    # head turned 3°, eyes at "middle"
    probe[3] = 0.4
    assert model.classify(probe)[0] == "middle"
    assert MIN_STD.shape == (15,)


def test_save_and_load_roundtrip(tmp_path):
    X, y, centres, _ = clusters()
    GazeModel(X, y).save("Test Patient", folder=str(tmp_path))
    assert GazeModel.exists("Test Patient", folder=str(tmp_path))
    loaded = GazeModel.load("Test Patient", folder=str(tmp_path))
    assert loaded.classify(centres["middle"])[0] == "middle"
