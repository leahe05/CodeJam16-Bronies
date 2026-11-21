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


def test_save_and_load_roundtrip(tmp_path):
    X, y, centres, _ = clusters()
    GazeModel(X, y).save("Test Patient", folder=str(tmp_path))
    assert GazeModel.exists("Test Patient", folder=str(tmp_path))
    loaded = GazeModel.load("Test Patient", folder=str(tmp_path))
    assert loaded.classify(centres["middle"])[0] == "middle"
