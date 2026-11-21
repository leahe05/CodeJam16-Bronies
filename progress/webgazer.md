15:49 webgazer: /patient?gaze=web runs WebGazer offline (calibration = follow the dot, no clicks), sends the screen third to POST /api/gaze; main.py --gaze web uses it (eyebrows stay in Python); big three-column layout; 169 tests pass
15:57 webgazer: calibration shows 'Starting the camera…' at once, a 25 s timeout and an on-screen error (camera busy / blocked) instead of silently waiting; opaque calibration screen; verified the dot runs with a test camera
16:06 webgazer: eyebrows + eyes-open read in the browser from WebGazer's face points, with their own calibration after the dot (B = redo); main.py --gaze web needs no Python camera; home = Room Controls left, greeting centre, Talk to AI right; 171 tests pass
16:15 webgazer: gaze frozen while the eyebrows rise (+0.7 s after), so a raise no longer moves the selection; Room Controls = one switch per device (Turn the lamp on/off, Turn the fan on/off, label follows the state); 172 tests pass
16:25 webgazer: big cards fill the screen height; background dims while a card is selected
16:32 webgazer: longer dot calibration (17 points) + left/centre/right check that sets the zone borders
