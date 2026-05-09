# Wonder Dash Gamepad Controller & Bridge API Update

This project is an intermediate summary of the enhancements made to the Wonder Dash robotics bridge. It includes a custom gamepad controller script running on a Raspberry Pi, integrated with a FastAPI web bridge for the Wonder Dash robot.

## Features

- **Gamepad Control (PS4/PS5 Controllers)**:
  - Connect a Bluetooth gamepad to steer the robot.
  - Automatically filters out virtual interfaces (Touchpad, Motion Sensors) to reliably attach to the main `evdev` input.
  - **L3 (Left Stick Click)**: Instantly stops the robot.
  - **R3 (Right Stick Click)**: Centers the robot's head (Yaw: 0, Pitch: 0).
  - Left Analog Stick for omnidirectional movement.
  - Right Analog Stick for head look (yaw/pitch).
  - L2/R2 to control movement speed.
  - D-Pad (Up, Down, Left, Right) to trigger specific light patterns.
  - Face Buttons (Cross, Circle, Triangle, Square) and Bumpers (L1, R1) to trigger sound and ear light effects.
- **Web UI & API Integration**:
  - The Python `gamepad_controller.py` runs as a systemd service (`wonder-dash-gamepad.service`) in the background.
  - It communicates via HTTP POST to the local FastAPI bridge (`wonder-dash-body.service`).
  - Periodically pings the `/gamepad/ping` endpoint to report connection status.
  - **Web Dashboard**: The `app.py` UI displays real-time `PAD CONNECTED` / `PAD DISCONNECTED` badges based on the active Bluetooth controller status.
- **Movement Logic Fixes**:
  - Re-mapped "Turn Left" and "Turn Right" API actions to use the robot's native `.spin()` method. This fixes previous issues where the robot would drive in wide arcs or move forward unnecessarily instead of rotating in place.

## Files Updated
- `gamepad_controller.py`: The `evdev`-based background service connecting the PS4/PS5 controller to the HTTP API.
- `app.py`: FastAPI server serving the control dashboard and bridging HTTP commands to the Dash Python SDK.
- `driver_adapter.py`: Adapter layer handling actual hardware commands, specifically improved for accurate in-place spinning.

## Systemd Services Setup
```ini
# /etc/systemd/system/wonder-dash-gamepad.service
[Unit]
Description=Wonder Dash Gamepad Controller
After=wonder-dash-body.service

[Service]
User=root
WorkingDirectory=/home/pi/wonder-dash-body
Environment="PATH=/home/pi/wonder-dash-body/.venv/bin"
ExecStart=/home/pi/wonder-dash-body/.venv/bin/python gamepad_controller.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

## Note on Privacy
This repository only contains the logic improvements and integration code. It does not include hardcoded MAC addresses, sensitive local network IPs, or personal identifiers.
