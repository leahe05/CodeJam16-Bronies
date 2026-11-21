# p2-brain progress

11:38 Rules PR: pull requests + code review; per-branch progress files
12:10 API contract: docs/API.md + fake_api (py -3.11 -m lookat.fake_api) so the pages can be built without the camera
12:14 B3: selection engine (machine.py), patient screens (screens.py), demo clock (clock.py); 75 tests pass
12:30 Frontend plumbing: lookat/server.py finds Lea's pages in web/ (plain, folders or web/dist) and uses them automatically; bare test page in lookat/fallback until then; check_frontend; 84 tests
12:38 M2-1 (code): main.py camera loop + server, live.py backend, plugs.py + devices.py (lamp, fan, temperature model), long raise no longer also chooses; 102 tests; keyboard mode end-to-end OK. Live camera + real lamp check still to do
12:55 Plug self-test PASS on lea: lamp 99-132 ms per switch
12:58 Live run 1: gaze calib 95% self-check (separation 4.3-5.6), brow threshold 0.64. Fixed: frozen calibration windows hid the live one; Kasa login read from saved user env
13:03 Gaze fix: calibrate on the card positions (2 rounds, honest round check), spread floor so small head turns don't beat the eyes; eyebrow feedback (operator window + brow in /api/screen); 104 tests
