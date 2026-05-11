"""
PS4 / DualShock 4 gamepad controller for Wonder Dash.
Reads button bindings from config/gamepad_bindings.json (hot-reload every 10s).
Uses the non-blocking /drive endpoint for left-stick movement.
"""
import evdev
import time
import json
import urllib.request
import threading
import os
from pathlib import Path

API_URL = os.getenv("WONDER_DASH_API", "http://127.0.0.1:8787")
CONFIG_FILE = Path(__file__).resolve().parent / "config" / "gamepad_bindings.json"
CONFIG_RELOAD_INTERVAL = 0.5  # seconds

# ── Default bindings (used if config file missing) ──

DEFAULT_BINDINGS = {
    "buttons": {
        "cross": {"action": "speak", "params": {"text": "siren"}},
        "circle": {"action": "speak", "params": {"text": "hi"}},
        "triangle": {"action": "speak", "params": {"text": "dino"}},
        "square": {"action": "speak", "params": {"text": "tada"}},
        "l1": {"action": "lights_blink", "params": {"ear": "left", "color": "yellow"}},
        "r1": {"action": "lights_blink", "params": {"ear": "right", "color": "yellow"}},
        "dpad_up": {"action": "lights_and_speak", "params": {"color": "red", "text": "siren"}},
        "dpad_down": {"action": "lights", "params": {"neck_color": "blue", "left_ear_color": "blue", "right_ear_color": "blue"}},
        "dpad_left": {"action": "lights", "params": {"neck_color": "green", "left_ear_color": "green", "right_ear_color": "green"}},
        "dpad_right": {"action": "lights", "params": {"neck_color": "black", "left_ear_color": "black", "right_ear_color": "black", "eye_brightness": 0}},
    }
}

# ── Config management ──

_bindings = dict(DEFAULT_BINDINGS)
_config_mtime = 0.0


def load_config():
    global _bindings, _config_mtime
    try:
        if CONFIG_FILE.exists():
            mtime = CONFIG_FILE.stat().st_mtime
            if mtime != _config_mtime:
                _bindings = json.loads(CONFIG_FILE.read_text())
                _config_mtime = mtime
                print(f"[config] Reloaded gamepad bindings ({len(_bindings.get('buttons', {}))} buttons)")
    except Exception as e:
        print(f"[config] Error loading config: {e}")


def get_binding(button_name: str) -> dict:
    return _bindings.get("buttons", {}).get(button_name, {"action": "none", "params": {}})


# ── API helpers ──

def send_post(endpoint, payload):
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{API_URL}/{endpoint}", data=data,
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        pass


def fire_and_forget(endpoint, payload):
    threading.Thread(target=send_post, args=(endpoint, payload), daemon=True).start()


# ── Action executors ──

def maybe_reload_config(last_check_time: float) -> float:
    now = time.time()
    if now - last_check_time > CONFIG_RELOAD_INTERVAL:
        load_config()
        return now
    return last_check_time


def execute_binding(binding: dict):
    """Execute a button binding action."""
    action = binding.get("action", "none")
    params = binding.get("params", {})

    if action == "none":
        return

    elif action == "speak":
        fire_and_forget("speak", {"text": params.get("text", "hi")})

    elif action == "lights":
        fire_and_forget("lights", {
            k: v for k, v in params.items()
            if k in ("neck_color", "left_ear_color", "right_ear_color", "tail_brightness", "eye_brightness")
        })

    elif action == "lights_blink":
        ear = params.get("ear", "left")
        color = params.get("color", "yellow")
        threading.Thread(target=blink_ear, args=(ear, color), daemon=True).start()

    elif action == "lights_and_speak":
        color = params.get("color", "red")
        fire_and_forget("lights", {
            "neck_color": color,
            "left_ear_color": color,
            "right_ear_color": color,
            "eye_brightness": 100,
        })
        fire_and_forget("speak", {"text": params.get("text", "siren")})

    elif action == "look":
        fire_and_forget("look", {
            "yaw": params.get("yaw", 0),
            "pitch": params.get("pitch", 0),
        })

    elif action == "move":
        fire_and_forget("move", {
            "action": params.get("move_action", "forward"),
            "duration_ms": params.get("duration_ms", 500),
            "speed": params.get("speed", 120),
        })

    elif action == "wander":
        fire_and_forget("wander", {
            "duration_s": params.get("duration_s", 10),
        })


def blink_ear(ear, color):
    ears = []
    if ear in ("left", "both"):
        ears.append("left")
    if ear in ("right", "both"):
        ears.append("right")
    for _ in range(3):
        for e in ears:
            fire_and_forget("lights", {f"{e}_ear_color": color})
        time.sleep(0.3)
        for e in ears:
            fire_and_forget("lights", {f"{e}_ear_color": "black"})
        time.sleep(0.3)
    for e in ears:
        fire_and_forget("lights", {f"{e}_ear_color": "green"})


# ── Device discovery ──

def find_ds4():
    try:
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        preferred = []
        fallback = []
        for device in devices:
            name = device.name.lower()
            if not ("wireless" in name or "playstation" in name or "ds4" in name or "sony" in name):
                continue
            if "touchpad" in name or "motion" in name:
                fallback.append(device)
                continue
            preferred.append(device)
        if preferred:
            return preferred[0]
        if fallback:
            return fallback[0]
    except Exception:
        pass
    return None


# ── Button code mapping ──

BUTTON_MAP = {
    304: "cross",      # BTN_SOUTH
    305: "circle",     # BTN_EAST
    307: "triangle",   # BTN_NORTH
    308: "square",     # BTN_WEST
    310: "l1",         # BTN_TL
    311: "r1",         # BTN_TR
}


# ── Main loop ──

def main():
    print("Gamepad controller started (config-driven)...")
    load_config()

    dev = None
    while dev is None:
        dev = find_ds4()
        if not dev:
            time.sleep(2)

    print(f"Connected to {dev.name} ({dev.path})")
    current_action = "stop"
    base_speed = 150
    current_speed = 150

    last_yaw, last_pitch = 0, 0
    ls_y, ls_x = 128, 128
    last_config_check = 0.0
    right_stick_deadzone = 18

    try:
        for event in dev.read_loop():
            # Periodic config reload
            last_config_check = maybe_reload_config(last_config_check)

            # ── Button press events ──
            if event.type == evdev.ecodes.EV_KEY and event.value == 1:
                btn_name = BUTTON_MAP.get(event.code)
                if btn_name:
                    binding = get_binding(btn_name)
                    execute_binding(binding)

            # ── Axis events ──
            elif event.type == evdev.ecodes.EV_ABS:
                # D-pad
                if event.code == evdev.ecodes.ABS_HAT0Y:
                    if event.value == -1:
                        execute_binding(get_binding("dpad_up"))
                    elif event.value == 1:
                        execute_binding(get_binding("dpad_down"))
                elif event.code == evdev.ecodes.ABS_HAT0X:
                    if event.value == -1:
                        execute_binding(get_binding("dpad_left"))
                    elif event.value == 1:
                        execute_binding(get_binding("dpad_right"))

                # Speed modifiers: L2 = slow, R2 = fast
                elif event.code == evdev.ecodes.ABS_Z:
                    current_speed = 80 if event.value > 100 else base_speed
                elif event.code == evdev.ecodes.ABS_RZ:
                    current_speed = 250 if event.value > 100 else base_speed

                # Right stick → head look
                elif event.code == evdev.ecodes.ABS_RX:
                    if abs(event.value - 128) > right_stick_deadzone:
                        yaw = int(((event.value - 128) / 128.0) * -53)
                        if abs(yaw - last_yaw) > 5:
                            fire_and_forget("look", {"yaw": yaw, "pitch": last_pitch})
                            last_yaw = yaw
                elif event.code == evdev.ecodes.ABS_RY:
                    if abs(event.value - 128) > right_stick_deadzone:
                        pitch = int(((128 - event.value) / 128.0) * 10)
                        if abs(pitch - last_pitch) > 2:
                            fire_and_forget("look", {"yaw": last_yaw, "pitch": pitch})
                            last_pitch = pitch

                # Left stick → movement (non-blocking /drive)
                elif event.code == evdev.ecodes.ABS_Y:
                    ls_y = event.value
                elif event.code == evdev.ecodes.ABS_X:
                    ls_x = event.value

                # Determine desired movement from left stick position
                new_action = "stop"
                if ls_y < 60:
                    new_action = "forward"
                elif ls_y > 195:
                    new_action = "backward"
                elif ls_x < 60:
                    new_action = "turn_left"
                elif ls_x > 195:
                    new_action = "turn_right"

                if new_action != current_action:
                    if new_action == "stop":
                        fire_and_forget("stop", {})
                    else:
                        fire_and_forget("drive", {"action": new_action, "speed": current_speed})
                    current_action = new_action
                elif new_action != "stop" and event.code in [evdev.ecodes.ABS_Z, evdev.ecodes.ABS_RZ]:
                    # Speed changed while moving — update
                    fire_and_forget("drive", {"action": new_action, "speed": current_speed})

    except Exception as e:
        print("Error:", e)


if __name__ == "__main__":
    while True:
        main()
        time.sleep(2)
