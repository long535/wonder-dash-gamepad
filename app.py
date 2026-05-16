import asyncio
import json
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from driver_adapter import build_driver

BASE_DIR = Path(__file__).resolve().parent
ROUTES_FILE = BASE_DIR / "config" / "routes.example.yaml"
GAMEPAD_CONFIG_FILE = BASE_DIR / "config" / "gamepad_bindings.json"
DRIVER_MODE = os.getenv("WONDER_DASH_DRIVER", "mock")
DASH_ADDRESS = os.getenv("WONDER_DASH_ADDRESS")
ESP32_CAM_STREAM_URL = os.getenv("ESP32_CAM_STREAM_URL", "http://192.168.1.204/stream").strip()
ESP32_CAM_SNAPSHOT_URL = os.getenv("ESP32_CAM_SNAPSHOT_URL", "http://192.168.1.204/capture").strip()
ESP32_CAM_PAGE_URL = os.getenv("ESP32_CAM_PAGE_URL", "http://192.168.1.204/").strip()

app = FastAPI(title="Wonder Dash Bridge", version="1.01")


# ── Pydantic Models ──────────────────────────────────────────

class DriveRequest(BaseModel):
    action: str
    speed: int = Field(default=150, ge=0, le=300)


class MoveRequest(BaseModel):
    action: str
    duration_ms: int = Field(ge=0, le=20000)
    speed: int = Field(default=120, ge=0, le=300)


class RouteRequest(BaseModel):
    name: str


class WanderRequest(BaseModel):
    duration_s: int = Field(default=30, ge=1, le=600)


class LightsRequest(BaseModel):
    neck_color: Optional[str] = None
    left_ear_color: Optional[str] = None
    right_ear_color: Optional[str] = None
    tail_brightness: Optional[int] = Field(default=None, ge=0, le=255)
    eye_brightness: Optional[int] = Field(default=None, ge=0, le=255)


class LookRequest(BaseModel):
    yaw: Optional[int] = Field(default=None, ge=-120, le=120)
    pitch: Optional[int] = Field(default=None, ge=-20, le=120)


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class ObstacleRequest(BaseModel):
    enabled: bool = True
    threshold: int = Field(default=15, ge=1, le=255)


# ── State ────────────────────────────────────────────────────

class BridgeState:
    def __init__(self) -> None:
        self.connected = False
        self.driver = DRIVER_MODE
        self.busy = False  # only used for blocking ops (route/wander)
        self.last_action: Optional[Dict[str, Any]] = None
        self.started_at = time.time()


state = BridgeState()
driver = build_driver(state=state, mode=DRIVER_MODE, address=DASH_ADDRESS)


def load_routes() -> Dict[str, List[Dict[str, Any]]]:
    if not ROUTES_FILE.exists():
        return {}
    data = yaml.safe_load(ROUTES_FILE.read_text()) or {}
    return data.get("routes", {})


def load_gamepad_config() -> Dict[str, Any]:
    if not GAMEPAD_CONFIG_FILE.exists():
        return get_default_gamepad_config()
    try:
        return json.loads(GAMEPAD_CONFIG_FILE.read_text())
    except Exception:
        return get_default_gamepad_config()


def get_default_gamepad_config() -> Dict[str, Any]:
    return {
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


def save_gamepad_config(config: Dict[str, Any]) -> None:
    GAMEPAD_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    GAMEPAD_CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False))


# ── Startup ──────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    try:
        await driver.connect()
    except Exception as exc:
        state.connected = False
        state.last_action = {
            "action": "startup_connect_failed",
            "driver": state.driver,
            "error": f"{type(exc).__name__}: {exc}",
            "at": time.time(),
        }


# ── API Endpoints ────────────────────────────────────────────

@app.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "driver": state.driver,
        "connected": state.connected,
        "busy": state.busy,
        "uptime_s": int(time.time() - state.started_at),
        "last_action": state.last_action,
        "routes": sorted(load_routes().keys()),
        "dash_address": DASH_ADDRESS,
        "camera": {
            "stream_url": ESP32_CAM_STREAM_URL,
            "snapshot_url": ESP32_CAM_SNAPSHOT_URL,
            "page_url": ESP32_CAM_PAGE_URL,
            "configured": bool(ESP32_CAM_STREAM_URL or ESP32_CAM_SNAPSHOT_URL or ESP32_CAM_PAGE_URL),
        },
    }


@app.get("/sensors")
async def sensors() -> Dict[str, Any]:
    return driver.get_sensors()


@app.post("/reconnect")
async def reconnect() -> Dict[str, Any]:
    try:
        await asyncio.wait_for(driver.reconnect(), timeout=8.0)
        return {"ok": True, "connected": state.connected, "last_action": state.last_action}
    except asyncio.TimeoutError:
        state.connected = False
        state.last_action = {
            "action": "reconnect_timeout",
            "driver": state.driver,
            "address": DASH_ADDRESS,
            "at": time.time(),
        }
        return {"ok": False, "connected": False, "error": "reconnect timeout", "last_action": state.last_action}
    except Exception as exc:
        state.connected = False
        state.last_action = {
            "action": "reconnect_failed",
            "driver": state.driver,
            "address": DASH_ADDRESS,
            "error": f"{type(exc).__name__}: {exc}",
            "at": time.time(),
        }
        return {"ok": False, "connected": False, "error": str(exc), "last_action": state.last_action}


@app.post("/stop")
async def stop() -> Dict[str, Any]:
    await driver.stop()
    state.busy = False
    return {"ok": True, "stopped": True}


@app.post("/drive")
async def drive(req: DriveRequest) -> Dict[str, Any]:
    """Non-blocking: start continuous drive. Returns immediately."""
    return await driver.drive_start(req.action, req.speed)


@app.post("/move")
async def move(req: MoveRequest) -> Dict[str, Any]:
    """Legacy blocking move (for routes/wander compatibility)."""
    if state.busy:
        raise HTTPException(status_code=409, detail="bridge busy")
    state.busy = True
    try:
        return await driver.move(req.action, req.duration_ms, req.speed)
    finally:
        state.busy = False


@app.post("/route")
async def route(req: RouteRequest) -> Dict[str, Any]:
    routes = load_routes()
    steps = routes.get(req.name)
    if not steps:
        raise HTTPException(status_code=404, detail="unknown route")
    if state.busy:
        raise HTTPException(status_code=409, detail="bridge busy")

    state.busy = True
    executed = []
    try:
        for step in steps:
            action = step.get("action", "stop")
            duration_ms = int(step.get("duration_ms", 0))
            speed = int(step.get("speed", 120))
            result = await driver.move(action, duration_ms, speed)
            executed.append(result)
        return {"ok": True, "route": req.name, "steps": executed}
    finally:
        state.busy = False


@app.post("/lights")
async def lights(req: LightsRequest) -> Dict[str, Any]:
    return await driver.lights(
        neck_color=req.neck_color,
        left_ear_color=req.left_ear_color,
        right_ear_color=req.right_ear_color,
        tail_brightness=req.tail_brightness,
        eye_brightness=req.eye_brightness,
    )


@app.post("/look")
async def look(req: LookRequest) -> Dict[str, Any]:
    return await driver.look(yaw=req.yaw, pitch=req.pitch)


@app.post("/speak")
async def speak(req: SpeakRequest) -> Dict[str, Any]:
    return await driver.speak(req.text)


@app.post("/wander")
async def wander(req: WanderRequest) -> Dict[str, Any]:
    if state.busy:
        raise HTTPException(status_code=409, detail="bridge busy")

    end_time = time.time() + req.duration_s
    state.busy = True
    executed = []
    choices = ["forward", "turn_left", "turn_right"]
    try:
        while time.time() < end_time:
            action = random.choice(choices)
            duration_ms = random.choice([300, 500, 800, 1000])
            speed = random.choice([120, 140, 160])
            result = await driver.move(action, duration_ms, speed)
            executed.append(result)
            await asyncio.sleep(0.15)
        await driver.stop()
        return {"ok": True, "mode": "wander", "count": len(executed), "steps": executed}
    finally:
        state.busy = False


@app.post("/obstacle")
async def obstacle(req: ObstacleRequest) -> Dict[str, Any]:
    if hasattr(driver, "set_obstacle_avoidance"):
        return await driver.set_obstacle_avoidance(req.enabled, req.threshold)
    return {"ok": False, "error": "not supported in mock mode"}


# ── Gamepad Config API ───────────────────────────────────────

@app.get("/gamepad/config")
async def gamepad_config_get() -> JSONResponse:
    return JSONResponse(load_gamepad_config())


@app.post("/gamepad/config")
async def gamepad_config_save(config: Dict[str, Any]) -> Dict[str, Any]:
    save_gamepad_config(config)
    return {"ok": True, "saved": True, "reloaded_within_s": 0.5}


@app.post("/gamepad/config/reset")
async def gamepad_config_reset() -> Dict[str, Any]:
    config = get_default_gamepad_config()
    save_gamepad_config(config)
    return {"ok": True, "reset": True}


# ── Available sounds list (for dropdown) ─────────────────────

@app.get("/sounds")
async def sounds_list() -> List[str]:
    try:
        from dash.constants import NOISES
        return sorted(NOISES.keys())
    except ImportError:
        return [
            "beep", "bragging", "buzz", "bye", "cat", "charge", "croc",
            "dino", "dog", "elephant", "engine", "gobble", "goat",
            "helicopter", "hi", "horn", "horse", "huh", "jet", "laser",
            "lion", "okay", "ohno", "siren", "squeek", "tada",
            "tiresqueal", "train", "wee", "yawn",
        ]


# ── Web UI ───────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def control_panel() -> str:
    routes = "".join(
        f'<button onclick="sendRoute(\'{name}\')">{name}</button>' for name in sorted(load_routes().keys())
    )
    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Wonder Dash Control Panel</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f1f5f9; margin: 0; padding: 16px; }}
    .wrap {{ max-width: 1100px; margin: 0 auto; }}
    .card {{ background: #1e293b; border-radius: 16px; padding: 20px; margin-bottom: 16px; box-shadow: 0 4px 16px rgba(0,0,0,.3); }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    h2 {{ margin: 0 0 12px; font-size: 17px; color: #94a3b8; }}
    .grid3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
    button {{ border: 0; border-radius: 10px; padding: 12px 16px; background: #334155; color: white; font-size: 15px; cursor: pointer; transition: background .15s; }}
    button:hover {{ background: #475569; }}
    button:active {{ background: #64748b; }}
    .stop {{ background: #dc2626 !important; }}
    .stop:hover {{ background: #ef4444 !important; }}
    .primary {{ background: #2563eb !important; }}
    .primary:hover {{ background: #3b82f6 !important; }}
    .success {{ background: #16a34a !important; }}
    .success:hover {{ background: #22c55e !important; }}
    .row {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 10px; }}
    .stack {{ display: grid; gap: 16px; }}
    .two-col {{ display: grid; grid-template-columns: 1.15fr 1fr; gap: 16px; align-items: start; }}
    input, select {{ padding: 8px 12px; border-radius: 8px; border: 1px solid #475569; background: #0f172a; color: white; font-size: 14px; }}
    input[type="number"] {{ width: 80px; }}
    input[type="text"], input[type="url"] {{ width: 220px; max-width: 100%; }}
    input[type="color"] {{ width: 50px; height: 36px; padding: 2px; cursor: pointer; }}
    select {{ min-width: 140px; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #0f172a; padding: 12px; border-radius: 10px; font-size: 13px; max-height: 220px; overflow-y: auto; }}
    label {{ font-size: 13px; color: #94a3b8; display: flex; align-items: center; gap: 6px; }}
    .badge {{ display: inline-block; padding: 4px 10px; border-radius: 999px; font-weight: 700; font-size: 12px; }}
    .badge.ok {{ background: #14532d; color: #bbf7d0; }}
    .badge.bad {{ background: #7f1d1d; color: #fecaca; }}
    .sensors-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 8px; }}
    .sensor-val {{ background: #0f172a; border-radius: 8px; padding: 8px; text-align: center; }}
    .sensor-val .lbl {{ font-size: 11px; color: #64748b; text-transform: uppercase; }}
    .sensor-val .val {{ font-size: 20px; font-weight: 700; }}
    .sensor-val .val.warn {{ color: #f59e0b; }}
    .sensor-val .val.danger {{ color: #ef4444; }}
    .gp-table {{ width: 100%; border-collapse: collapse; }}
    .gp-table th {{ text-align: left; padding: 8px; color: #64748b; font-size: 13px; border-bottom: 1px solid #334155; }}
    .gp-table td {{ padding: 8px; border-bottom: 1px solid #1e293b; vertical-align: middle; }}
    .gp-table td:first-child {{ font-weight: 600; white-space: nowrap; width: 120px; }}
    .gp-table select, .gp-table input {{ width: 100%; }}
    .gp-params {{ display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }}
    .gp-params input {{ width: 100px; }}
    .gp-params input[type="color"] {{ width: 40px; }}
    .camera-frame {{ width: 100%; aspect-ratio: 16 / 9; background: #020617; border: 1px solid #334155; border-radius: 14px; overflow: hidden; display: flex; align-items: center; justify-content: center; }}
    .camera-frame img, .camera-frame iframe {{ width: 100%; height: 100%; border: 0; object-fit: cover; background: #020617; }}
    .camera-placeholder {{ color: #94a3b8; font-size: 14px; text-align: center; padding: 24px; line-height: 1.6; }}
    .small {{ font-size: 12px; color: #64748b; }}
    .section-title {{ display: flex; justify-content: space-between; align-items: center; gap: 12px; }}
    @media (max-width: 900px) {{ .two-col {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <div class="wrap stack">
    <div class="card">
      <h1>🤖 Wonder Dash Control Panel</h1>
      <div class="row">
        <div id="badge" class="badge bad">DISCONNECTED</div>
        <button onclick="reconnect()">Reconnect</button>
        <button onclick="refresh()">Refresh</button>
        <button onclick="refreshCamera()">Refresh Camera</button>
      </div>
      <div style="font-size:12px;color:#64748b" id="status_line">loading...</div>
    </div>

    <div class="card">
      <div class="section-title">
        <h2 style="margin-bottom:0">👁️ ESP32-CAM Live View</h2>
        <div class="small" id="camera_status">camera loading...</div>
      </div>
      <div class="camera-frame" id="camera_frame">
        <div class="camera-placeholder">Loading camera…</div>
      </div>
      <div class="row" style="margin-top:12px">
        <button onclick="openCameraPage()">Open Camera Page</button>
        <label>Stream URL <input id="camera_url_input" type="url" placeholder="http://esp32-cam.local:81/stream" /></label>
        <button class="success" onclick="applyCameraUrl()">Use This Stream</button>
      </div>
      <div class="small">上方顯示 ESP32-CAM 實時影像；下方直接操控 Dash。同頁完成，不用切頁。</div>
    </div>

    <div class="two-col">
      <div class="stack">
        <div class="card">
          <h2>🕹️ Manual Control</h2>
          <div class="row">
            <label>Speed <input id="speed" type="number" value="150" /></label>
          </div>
          <div class="grid3">
            <div></div>
            <button class="primary" onpointerdown="driveStart('forward')" onpointerup="driveStop()" onpointerleave="driveStop()">▲ Forward</button>
            <div></div>
            <button onpointerdown="driveStart('turn_left')" onpointerup="driveStop()" onpointerleave="driveStop()">◀ Left</button>
            <button class="stop" onclick="driveStop()">■ Stop</button>
            <button onpointerdown="driveStart('turn_right')" onpointerup="driveStop()" onpointerleave="driveStop()">▶ Right</button>
            <div></div>
            <button onpointerdown="driveStart('backward')" onpointerup="driveStop()" onpointerleave="driveStop()">▼ Back</button>
            <div></div>
          </div>
          <div style="margin-top:12px;font-size:12px;color:#64748b">
            Hold to drive · Release to stop · Touch, mouse, keyboard supported
          </div>
        </div>

        <div class="card">
          <h2>Timed Move / Routes / Wander</h2>
          <div class="row">
            <label>Duration ms <input id="duration" type="number" value="500" /></label>
            <label>Move Speed <input id="move_speed" type="number" value="120" /></label>
          </div>
          <div class="row">
            <button onclick="timedMove('forward')">Fwd</button>
            <button onclick="timedMove('backward')">Back</button>
            <button onclick="timedMove('turn_left')">Left</button>
            <button onclick="timedMove('turn_right')">Right</button>
          </div>
          <div class="row" style="margin-top:14px">{routes}</div>
          <div class="row" style="margin-top:14px">
            <label>Wander s <input id="wander_s" type="number" value="10" /></label>
            <button onclick="wander()">Start Wander</button>
          </div>
        </div>

        <div class="card">
          <h2>💡 Lights / Look / Speak</h2>
          <div class="row">
            <label>Neck <input id="neck_color" type="color" value="#0088ff" /></label>
            <label>Left Ear <input id="left_ear_color" type="color" value="#ff66aa" /></label>
            <label>Right Ear <input id="right_ear_color" type="color" value="#66ff99" /></label>
          </div>
          <div class="row">
            <label>Tail <input id="tail_brightness" type="number" value="180" /></label>
            <label>Eyes <input id="eye_brightness" type="number" value="180" /></label>
            <button class="success" onclick="setLights()">Apply Lights</button>
            <button onclick="lightsOff()">All Off</button>
          </div>
          <div class="row">
            <button onclick="presetLights('red')">🔴</button>
            <button onclick="presetLights('green')">🟢</button>
            <button onclick="presetLights('blue')">🔵</button>
            <button onclick="presetLights('purple')">🟣</button>
            <button onclick="presetLights('white')">⚪</button>
            <button onclick="presetLights('rainbow')">🌈</button>
          </div>
          <div class="row" style="margin-top:14px">
            <label>Yaw <input id="yaw" type="number" value="0" /></label>
            <label>Pitch <input id="pitch" type="number" value="0" /></label>
            <button onclick="look()">Look</button>
            <button onclick="lookCenter()">Center</button>
          </div>
          <div class="row">
            <select id="speak_text" style="min-width:200px">
              <option value="hi">hi</option>
              <option value="siren">siren</option>
              <option value="dino">dino</option>
              <option value="tada">tada</option>
            </select>
            <button onclick="speak()">Speak</button>
          </div>
        </div>
      </div>

      <div class="stack">
        <div class="card">
          <h2>📡 Live Sensors</h2>
          <div class="sensors-grid" id="sensors_grid">
            <div class="sensor-val"><div class="lbl">Prox Left</div><div class="val" id="s_prox_left">-</div></div>
            <div class="sensor-val"><div class="lbl">Prox Right</div><div class="val" id="s_prox_right">-</div></div>
            <div class="sensor-val"><div class="lbl">Prox Rear</div><div class="val" id="s_prox_rear">-</div></div>
            <div class="sensor-val"><div class="lbl">Yaw</div><div class="val" id="s_yaw">-</div></div>
            <div class="sensor-val"><div class="lbl">Pitch</div><div class="val" id="s_pitch">-</div></div>
            <div class="sensor-val"><div class="lbl">Roll</div><div class="val" id="s_roll">-</div></div>
            <div class="sensor-val"><div class="lbl">Moving</div><div class="val" id="s_moving">-</div></div>
            <div class="sensor-val"><div class="lbl">Picked Up</div><div class="val" id="s_picked_up">-</div></div>
            <div class="sensor-val"><div class="lbl">Wheel Dist</div><div class="val" id="s_wheel_distance">-</div></div>
            <div class="sensor-val"><div class="lbl">Mic Level</div><div class="val" id="s_mic_level">-</div></div>
          </div>
        </div>

        <div class="card">
          <h2>Obstacle Avoidance</h2>
          <div class="row">
            <label>Enabled <input id="obstacle_enabled" type="checkbox" checked /></label>
            <label>Threshold <input id="obstacle_threshold" type="number" value="15" /></label>
            <button class="success" onclick="setObstacle()">Apply</button>
          </div>
          <div class="small">When enabled, Dash auto-stops if proximity sensor detects obstacle while driving.</div>
        </div>

        <div class="card">
          <h2>🎮 PS4 Controller Button Mapping</h2>
          <p class="small" style="margin-top:0">Customise what each button does. Changes are saved to Pi and loaded by the gamepad controller.</p>
          <table class="gp-table" id="gp_table">
            <thead><tr><th>Button</th><th>Action</th><th>Parameters</th></tr></thead>
            <tbody id="gp_tbody"></tbody>
          </table>
          <div class="row" style="margin-top:12px">
            <button class="success" onclick="saveGamepadConfig()">💾 Save Config</button>
            <button onclick="resetGamepadConfig()">↩️ Reset to Default</button>
            <button onclick="loadGamepadConfig()">🔄 Reload</button>
          </div>
          <div id="gp_save_status" style="font-size:12px;color:#22c55e;margin-top:4px"></div>
        </div>

        <div class="card">
          <h2>Last Response</h2>
          <pre id="output">Ready.</pre>
        </div>
      </div>
    </div>
  </div>

<script>
let cameraConfig = {{ stream_url: '', snapshot_url: '', page_url: '', configured: false }};

function renderCamera() {{
  const frame = document.getElementById('camera_frame');
  const status = document.getElementById('camera_status');
  const input = document.getElementById('camera_url_input');
  const streamUrl = input.value.trim() || cameraConfig.stream_url;
  if (cameraConfig.stream_url && !input.value.trim()) input.value = cameraConfig.stream_url;

  if (streamUrl) {{
    frame.innerHTML = `<img id="camera_stream" src="${{streamUrl}}?t=${{Date.now()}}" alt="ESP32-CAM stream" referrerpolicy="no-referrer" />`;
    status.textContent = 'Live stream configured';
    return;
  }}
  if (cameraConfig.snapshot_url) {{
    frame.innerHTML = `<img id="camera_stream" src="${{cameraConfig.snapshot_url}}?t=${{Date.now()}}" alt="ESP32-CAM snapshot" referrerpolicy="no-referrer" />`;
    status.textContent = 'Snapshot mode';
    return;
  }}
  if (cameraConfig.page_url) {{
    frame.innerHTML = `<iframe src="${{cameraConfig.page_url}}" title="ESP32-CAM page"></iframe>`;
    status.textContent = 'Embedded camera page';
    return;
  }}
  frame.innerHTML = `<div class="camera-placeholder">尚未設定 ESP32-CAM 串流 URL。<br>刷完 firmware 後，把 stream URL 填進來即可。</div>`;
  status.textContent = 'Camera not configured yet';
}}

function refreshCamera() {{
  const img = document.getElementById('camera_stream');
  if (img && img.tagName === 'IMG') {{
    const base = (document.getElementById('camera_url_input').value.trim() || cameraConfig.stream_url || cameraConfig.snapshot_url || '').split('?')[0];
    if (base) img.src = `${{base}}?t=${{Date.now()}}`;
  }} else {{
    renderCamera();
  }}
}}

function applyCameraUrl() {{
  const input = document.getElementById('camera_url_input').value.trim();
  if (!input) return;
  cameraConfig.stream_url = input;
  renderCamera();
}}

function openCameraPage() {{
  const url = cameraConfig.page_url || cameraConfig.stream_url || cameraConfig.snapshot_url || document.getElementById('camera_url_input').value.trim();
  if (url) window.open(url, '_blank', 'noopener');
}}

// ── Generic API call ──
async function api(path, body) {{
  try {{
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const res = await fetch(path, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(body),
      signal: controller.signal
    }});
    clearTimeout(timeout);
    const text = await res.text();
    document.getElementById('output').textContent = text;
    return JSON.parse(text);
  }} catch (e) {{
    document.getElementById('output').textContent = 'Error: ' + e.message;
    return null;
  }}
}}

// ── Generic API call ──
async function api(path, body) {{
  try {{
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    const res = await fetch(path, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(body),
      signal: controller.signal
    }});
    clearTimeout(timeout);
    const text = await res.text();
    document.getElementById('output').textContent = text;
    return JSON.parse(text);
  }} catch (e) {{
    document.getElementById('output').textContent = 'Error: ' + e.message;
    return null;
  }}
}}

// ── Drive (non-blocking) ──
let driving = false;
async function driveStart(action) {{
  if (driving) return;
  driving = true;
  const speed = parseInt(document.getElementById('speed').value || '150', 10);
  await api('/drive', {{ action, speed }});
}}
async function driveStop() {{
  if (!driving) return;
  driving = false;
  await api('/stop', {{}});
}}

// Prevent context menu on long-press (mobile)
document.addEventListener('contextmenu', e => {{
  if (e.target.closest && e.target.closest('.grid3')) e.preventDefault();
}});

// ── Timed move (legacy) ──
async function timedMove(action) {{
  const dur = parseInt(document.getElementById('duration').value || '500', 10);
  const spd = parseInt(document.getElementById('move_speed').value || '120', 10);
  await api('/move', {{ action, duration_ms: dur, speed: spd }});
}}

// ── Other controls ──
async function reconnect() {{
  document.getElementById('output').textContent = 'Reconnecting...';
  const result = await api('/reconnect', {{}});
  await refresh();
  return result;
}}
async function sendRoute(name) {{ await api('/route', {{ name }}); }}
async function wander() {{ await api('/wander', {{ duration_s: parseInt(document.getElementById('wander_s').value || '10', 10) }}); }}

function hexToName(hex) {{ return hex; }}

async function setLights() {{
  await api('/lights', {{
    neck_color: document.getElementById('neck_color').value,
    left_ear_color: document.getElementById('left_ear_color').value,
    right_ear_color: document.getElementById('right_ear_color').value,
    tail_brightness: parseInt(document.getElementById('tail_brightness').value || '180', 10),
    eye_brightness: parseInt(document.getElementById('eye_brightness').value || '180', 10)
  }});
}}

async function lightsOff() {{
  await api('/lights', {{ neck_color: 'black', left_ear_color: 'black', right_ear_color: 'black', tail_brightness: 0, eye_brightness: 0 }});
}}

async function presetLights(color) {{
  const presets = {{
    red: {{ neck_color: 'red', left_ear_color: 'red', right_ear_color: 'red' }},
    green: {{ neck_color: 'green', left_ear_color: 'green', right_ear_color: 'green' }},
    blue: {{ neck_color: 'blue', left_ear_color: 'blue', right_ear_color: 'blue' }},
    purple: {{ neck_color: 'purple', left_ear_color: 'purple', right_ear_color: 'purple' }},
    white: {{ neck_color: 'white', left_ear_color: 'white', right_ear_color: 'white' }},
    rainbow: {{ neck_color: 'red', left_ear_color: 'green', right_ear_color: 'blue' }},
  }};
  await api('/lights', presets[color]);
}}

async function look() {{
  await api('/look', {{
    yaw: parseInt(document.getElementById('yaw').value || '0', 10),
    pitch: parseInt(document.getElementById('pitch').value || '0', 10)
  }});
}}
async function lookCenter() {{
  document.getElementById('yaw').value = '0';
  document.getElementById('pitch').value = '0';
  await look();
}}

async function speak() {{
  await api('/speak', {{ text: document.getElementById('speak_text').value }});
}}

async function setObstacle() {{
  const enabled = document.getElementById('obstacle_enabled').checked;
  const threshold = parseInt(document.getElementById('obstacle_threshold').value || '15', 10);
  await api('/obstacle', {{ enabled, threshold }});
}}

// ── Sensor polling ──
let sensorInterval = null;
function startSensorPoll() {{
  if (sensorInterval) return;
  pollSensors();
  sensorInterval = setInterval(pollSensors, 500);
}}
function stopSensorPoll() {{
  if (sensorInterval) {{ clearInterval(sensorInterval); sensorInterval = null; }}
}}

async function pollSensors() {{
  try {{
    const res = await fetch('/sensors');
    const d = await res.json();
    const threshold = parseInt(document.getElementById('obstacle_threshold').value || '15', 10);
    for (const [key, val] of Object.entries(d)) {{
      const el = document.getElementById('s_' + key);
      if (!el) continue;
      el.textContent = typeof val === 'boolean' ? (val ? '✅' : '—') : val;
      // Color code proximity
      if (key.startsWith('prox_')) {{
        el.className = 'val' + (val > threshold ? ' danger' : val > threshold * 0.6 ? ' warn' : '');
      }}
    }}
  }} catch (e) {{}}
}}

// ── Status refresh ──
async function refresh() {{
  try {{
    const res = await fetch('/health');
    const d = await res.json();
    const badge = document.getElementById('badge');
    badge.textContent = d.connected ? 'CONNECTED' : 'DISCONNECTED';
    badge.className = 'badge ' + (d.connected ? 'ok' : 'bad');
    document.getElementById('status_line').textContent =
      `driver=${{d.driver}} | uptime=${{d.uptime_s}}s | busy=${{d.busy}} | routes=${{d.routes.join(', ')}}`;
    const last = d.last_action ? JSON.stringify(d.last_action).substring(0, 120) : 'none';
    document.getElementById('output').textContent = last;
    if (d.camera) {{
      cameraConfig = d.camera;
      const input = document.getElementById('camera_url_input');
      if (input && !input.value.trim()) {{
        input.value = d.camera.stream_url || d.camera.snapshot_url || d.camera.page_url || '';
      }}
      renderCamera();
    }}
  }} catch(e) {{}}
}}
refresh();
setInterval(refresh, 3000);

// ── Populate sounds dropdown ──
(async function() {{
  try {{
    const res = await fetch('/sounds');
    const sounds = await res.json();
    const sel = document.getElementById('speak_text');
    sel.innerHTML = '';
    for (const s of sounds) {{
      const opt = document.createElement('option');
      opt.value = s; opt.textContent = s;
      sel.appendChild(opt);
    }}
  }} catch(e) {{}}
}})();

// ── Gamepad config ──
const GP_BUTTONS = [
  {{ key: 'cross', label: '✕ Cross' }},
  {{ key: 'circle', label: '○ Circle' }},
  {{ key: 'triangle', label: '△ Triangle' }},
  {{ key: 'square', label: '□ Square' }},
  {{ key: 'l1', label: 'L1' }},
  {{ key: 'r1', label: 'R1' }},
  {{ key: 'dpad_up', label: 'D-pad ↑' }},
  {{ key: 'dpad_down', label: 'D-pad ↓' }},
  {{ key: 'dpad_left', label: 'D-pad ←' }},
  {{ key: 'dpad_right', label: 'D-pad →' }},
];
const GP_ACTIONS = ['speak', 'lights', 'lights_blink', 'lights_and_speak', 'look', 'move', 'wander', 'none'];

let gpConfig = {{}};

async function loadGamepadConfig() {{
  try {{
    const res = await fetch('/gamepad/config');
    gpConfig = await res.json();
    renderGamepadTable();
  }} catch(e) {{ console.error(e); }}
}}

function renderGamepadTable() {{
  const tbody = document.getElementById('gp_tbody');
  tbody.innerHTML = '';
  const buttons = gpConfig.buttons || {{}};
  for (const btn of GP_BUTTONS) {{
    const cfg = buttons[btn.key] || {{ action: 'none', params: {{}} }};
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${{btn.label}}</td>
      <td>
        <select onchange="gpActionChanged('${{btn.key}}', this.value)" data-btn="${{btn.key}}">
          ${{GP_ACTIONS.map(a => `<option value="${{a}}" ${{a === cfg.action ? 'selected' : ''}}>${{a}}</option>`).join('')}}
        </select>
      </td>
      <td><div class="gp-params" id="gp_params_${{btn.key}}"></div></td>
    `;
    tbody.appendChild(tr);
    renderGpParams(btn.key, cfg.action, cfg.params || {{}});
  }}
}}

function renderGpParams(btnKey, action, params) {{
  const container = document.getElementById('gp_params_' + btnKey);
  if (!container) return;
  container.innerHTML = '';
  if (action === 'speak') {{
    container.innerHTML = `<label>Sound <select data-btn="${{btnKey}}" data-param="text" class="gp-param"
      ><option>loading...</option></select></label>`;
    // populate from sounds
    fetch('/sounds').then(r => r.json()).then(sounds => {{
      const sel = container.querySelector('select');
      sel.innerHTML = sounds.map(s => `<option value="${{s}}" ${{s === (params.text||'') ? 'selected' : ''}}>${{s}}</option>`).join('');
    }}).catch(() => {{}});
  }} else if (action === 'lights') {{
    container.innerHTML = `
      <label>Neck <input type="color" data-param="neck_color" class="gp-param" value="${{params.neck_color || '#000000'}}" /></label>
      <label>L.Ear <input type="color" data-param="left_ear_color" class="gp-param" value="${{params.left_ear_color || '#000000'}}" /></label>
      <label>R.Ear <input type="color" data-param="right_ear_color" class="gp-param" value="${{params.right_ear_color || '#000000'}}" /></label>
      <label>Eyes <input type="number" data-param="eye_brightness" class="gp-param" value="${{params.eye_brightness ?? 180}}" style="width:60px" /></label>
    `;
  }} else if (action === 'lights_blink') {{
    container.innerHTML = `
      <label>Ear <select data-param="ear" class="gp-param">
        <option value="left" ${{params.ear==='left'?'selected':''}}>Left</option>
        <option value="right" ${{params.ear==='right'?'selected':''}}>Right</option>
        <option value="both" ${{params.ear==='both'?'selected':''}}>Both</option>
      </select></label>
      <label>Color <input type="color" data-param="color" class="gp-param" value="${{params.color || '#ffff00'}}" /></label>
    `;
  }} else if (action === 'lights_and_speak') {{
    container.innerHTML = `
      <label>Color <input type="color" data-param="color" class="gp-param" value="${{params.color || '#ff0000'}}" /></label>
      <label>Sound <select data-param="text" class="gp-param"><option>loading...</option></select></label>
    `;
    fetch('/sounds').then(r => r.json()).then(sounds => {{
      const sel = container.querySelector('select[data-param="text"]');
      sel.innerHTML = sounds.map(s => `<option value="${{s}}" ${{s === (params.text||'') ? 'selected' : ''}}>${{s}}</option>`).join('');
    }}).catch(() => {{}});
  }} else if (action === 'look') {{
    container.innerHTML = `
      <label>Yaw <input type="number" data-param="yaw" class="gp-param" value="${{params.yaw ?? 0}}" style="width:60px" /></label>
      <label>Pitch <input type="number" data-param="pitch" class="gp-param" value="${{params.pitch ?? 0}}" style="width:60px" /></label>
    `;
  }} else if (action === 'move') {{
    container.innerHTML = `
      <label>Direction <select data-param="move_action" class="gp-param">
        <option value="forward" ${{params.move_action==='forward'?'selected':''}}>Forward</option>
        <option value="backward" ${{params.move_action==='backward'?'selected':''}}>Backward</option>
        <option value="turn_left" ${{params.move_action==='turn_left'?'selected':''}}>Turn Left</option>
        <option value="turn_right" ${{params.move_action==='turn_right'?'selected':''}}>Turn Right</option>
      </select></label>
      <label>Duration <input type="number" data-param="duration_ms" class="gp-param" value="${{params.duration_ms ?? 500}}" style="width:70px" />ms</label>
    `;
  }} else if (action === 'wander') {{
    container.innerHTML = `
      <label>Duration <input type="number" data-param="duration_s" class="gp-param" value="${{params.duration_s ?? 10}}" style="width:60px" />s</label>
    `;
  }}
}}

function gpActionChanged(btnKey, action) {{
  if (!gpConfig.buttons) gpConfig.buttons = {{}};
  gpConfig.buttons[btnKey] = {{ action, params: {{}} }};
  renderGpParams(btnKey, action, {{}});
}}

function collectGamepadConfig() {{
  const config = {{ buttons: {{}} }};
  for (const btn of GP_BUTTONS) {{
    const sel = document.querySelector(`select[data-btn="${{btn.key}}"]`);
    if (!sel) continue;
    const action = sel.value;
    const params = {{}};
    const paramEls = document.querySelectorAll(`#gp_params_${{btn.key}} .gp-param`);
    for (const el of paramEls) {{
      const key = el.dataset.param;
      if (!key) continue;
      if (el.type === 'number') params[key] = parseInt(el.value || '0', 10);
      else params[key] = el.value;
    }}
    config.buttons[btn.key] = {{ action, params }};
  }}
  return config;
}}

async function saveGamepadConfig() {{
  const config = collectGamepadConfig();
  const res = await fetch('/gamepad/config', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(config)
  }});
  const data = await res.json();
  document.getElementById('gp_save_status').textContent = data.ok
    ? `✅ Saved! Live within ~${{data.reloaded_within_s ?? 0.5}}s`
    : '❌ Error';
  setTimeout(() => document.getElementById('gp_save_status').textContent = '', 3000);
}}

async function resetGamepadConfig() {{
  if (!confirm('Reset all button mappings to default?')) return;
  await fetch('/gamepad/config/reset', {{ method: 'POST' }});
  await loadGamepadConfig();
  document.getElementById('gp_save_status').textContent = '↩️ Reset to default';
  setTimeout(() => document.getElementById('gp_save_status').textContent = '', 3000);
}}

// ── Keyboard controls ──
document.addEventListener('keydown', (e) => {{
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (e.repeat) return;
  switch(e.key) {{
    case 'ArrowUp': case 'w': driveStart('forward'); break;
    case 'ArrowDown': case 's': driveStart('backward'); break;
    case 'ArrowLeft': case 'a': driveStart('turn_left'); break;
    case 'ArrowRight': case 'd': driveStart('turn_right'); break;
    case ' ': driveStop(); break;
  }}
}});
document.addEventListener('keyup', (e) => {{
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
  if (['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','w','a','s','d'].includes(e.key)) driveStop();
}});
</script>
</body>
</html>
"""
