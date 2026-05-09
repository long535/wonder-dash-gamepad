import evdev
import time
import json
import urllib.request
import threading

API_URL = "http://127.0.0.1:8787"

def send_post(endpoint, payload):
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(f"{API_URL}/{endpoint}", data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=0.5)
    except:
        pass

def fire_and_forget(endpoint, payload):
    threading.Thread(target=send_post, args=(endpoint, payload), daemon=True).start()

def find_ds4():
    try:
        devices = [evdev.InputDevice(path) for path in evdev.list_devices()]
        for device in devices:
            name = device.name.lower()
            if ("wireless" in name or "playstation" in name or "ds4" in name or "sony" in name) and "touchpad" not in name and "motion" not in name:
                return device
    except:
        pass
    return None

def blink_ear(ear):
    for _ in range(3):
        fire_and_forget("lights", {f"{ear}_ear_color": "yellow"})
        time.sleep(0.3)
        fire_and_forget("lights", {f"{ear}_ear_color": "black"})
        time.sleep(0.3)
    fire_and_forget("lights", {f"{ear}_ear_color": "green"}) # restore

def main():
    print("Gamepad controller started...")
    dev = None
    while dev is None:
        dev = find_ds4()
        if not dev: time.sleep(2)
    
    print(f"Connected to {dev.name}")
    current_action = "stop"
    base_speed = 150
    current_speed = 150
    
    last_yaw, last_pitch = 0, 0
    ls_y, ls_x = 128, 128
    
    
    def ping_loop():
        while True:
            fire_and_forget("gamepad/ping", {})
            time.sleep(2)
    threading.Thread(target=ping_loop, daemon=True).start()

    try:
        for event in dev.read_loop():
            if event.type == evdev.ecodes.EV_KEY and event.value == 1:
                if event.code in [evdev.ecodes.BTN_SOUTH, 304]: # Cross
                    fire_and_forget("speak", {"text": "siren"})
                elif event.code in [evdev.ecodes.BTN_EAST, 305]: # Circle
                    fire_and_forget("speak", {"text": "hi"})
                elif event.code in [evdev.ecodes.BTN_NORTH, 307]: # Triangle
                    fire_and_forget("speak", {"text": "dino"})
                elif event.code in [evdev.ecodes.BTN_WEST, 308]: # Square
                    fire_and_forget("speak", {"text": "tada"})
                elif event.code in [evdev.ecodes.BTN_TL, 310]: # L1
                    threading.Thread(target=blink_ear, args=("left",), daemon=True).start()
                elif event.code in [evdev.ecodes.BTN_TR, 311]: # R1
                    threading.Thread(target=blink_ear, args=("right",), daemon=True).start()

                elif event.code in [evdev.ecodes.BTN_THUMBL, 317]: # L3
                    fire_and_forget("move", {"action": "stop", "duration_ms": 0, "speed": 0})
                elif event.code in [evdev.ecodes.BTN_THUMBR, 318]: # R3
                    fire_and_forget("look", {"yaw": 0, "pitch": 0})

            elif event.type == evdev.ecodes.EV_ABS:
                if event.code == evdev.ecodes.ABS_HAT0Y:
                    if event.value == -1: # Up
                        fire_and_forget("lights", {"neck_color": "red", "left_ear_color": "red", "right_ear_color": "red", "eye_brightness": 100})
                        fire_and_forget("speak", {"text": "siren"})
                    elif event.value == 1: # Down
                        fire_and_forget("lights", {"neck_color": "blue", "left_ear_color": "blue", "right_ear_color": "blue", "eye_brightness": 100})
                elif event.code == evdev.ecodes.ABS_HAT0X:
                    if event.value == -1: # Left
                        fire_and_forget("lights", {"neck_color": "green", "left_ear_color": "green", "right_ear_color": "green", "eye_brightness": 100})
                    elif event.value == 1: # Right
                        fire_and_forget("lights", {"neck_color": "black", "left_ear_color": "black", "right_ear_color": "black", "eye_brightness": 0})
                
                # Speed L2/R2
                elif event.code == evdev.ecodes.ABS_Z:
                    current_speed = 80 if event.value > 100 else base_speed
                elif event.code == evdev.ecodes.ABS_RZ:
                    current_speed = 250 if event.value > 100 else base_speed

                # Head Right Stick
                elif event.code == evdev.ecodes.ABS_RX or event.code == evdev.ecodes.ABS_Z:
                    yaw = int(((event.value - 128) / 128.0) * -120)
                    if abs(yaw - last_yaw) > 10:
                        fire_and_forget("look", {"yaw": yaw, "pitch": last_pitch})
                        last_yaw = yaw
                elif event.code == evdev.ecodes.ABS_RY or event.code == evdev.ecodes.ABS_RZ:
                    pitch = int(((128 - event.value) / 128.0) * 20)
                    if abs(pitch - last_pitch) > 3:
                        fire_and_forget("look", {"yaw": last_yaw, "pitch": pitch})
                        last_pitch = pitch
                        
                # Left Stick
                elif event.code == evdev.ecodes.ABS_Y: ls_y = event.value
                elif event.code == evdev.ecodes.ABS_X: ls_x = event.value

                new_action = "stop"
                if ls_y < 60: new_action = "forward"
                elif ls_y > 195: new_action = "backward"
                elif ls_x < 60: new_action = "turn_left"
                elif ls_x > 195: new_action = "turn_right"
                
                if new_action != current_action:
                    if new_action == "stop":
                        fire_and_forget("move", {"action": "stop"})
                    else:
                        fire_and_forget("move", {"action": new_action, "speed": current_speed, "duration_ms": 5000})
                    current_action = new_action
                elif new_action != "stop" and event.code in [evdev.ecodes.ABS_Z, evdev.ecodes.ABS_RZ]:
                    fire_and_forget("move", {"action": new_action, "speed": current_speed, "duration_ms": 5000})
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    while True:
        main()
        time.sleep(2)
