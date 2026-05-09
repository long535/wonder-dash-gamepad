import asyncio
import os
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from driver_adapter import build_driver

BASE_DIR = Path(__file__).resolve().parent
ROUTES_FILE = BASE_DIR / "config" / "routes.example.yaml"
DRIVER_MODE = os.getenv("WONDER_DASH_DRIVER", "mock")
DASH_ADDRESS = os.getenv("WONDER_DASH_ADDRESS")

app = FastAPI(title="Wonder Dash Bridge", version="0.3.0")


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


class BridgeState:
    def __init__(self) -> None:
        self.connected = False
        self.driver = DRIVER_MODE
        self.busy = False
        self.last_action: Optional[Dict[str, Any]] = None
        self.started_at = time.time()
        self.last_gamepad_ping = 0.0


state = BridgeState()
driver = build_driver(state=state, mode=DRIVER_MODE, address=DASH_ADDRESS)


def load_routes() -> Dict[str, List[Dict[str, Any]]]:
    if not ROUTES_FILE.exists():
        return {}
    data = yaml.safe_load(ROUTES_FILE.read_text()) or {}
    return data.get("routes", {})


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


@app.get("/", response_class=HTMLResponse)
async def control_panel() -> str:
    routes = "".join(
        f'<button onclick="sendRoute(\'{name}\')">{name}</button>' for name in sorted(load_routes().keys())
    )
    return f"""
<!doctype html>
<html>
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Wonder Dash Control Panel</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #111827; color: #f9fafb; margin: 0; padding: 24px; }}
    .wrap {{ max-width: 920px; margin: 0 auto; }}
    .card {{ background: #1f2937; border-radius: 16px; padding: 20px; margin-bottom: 16px; box-shadow: 0 8px 24px rgba(0,0,0,.25); }}
    h1, h2 {{ margin-top: 0; }}
    .grid {{ display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 12px; }}
    button {{ border: 0; border-radius: 12px; padding: 14px 16px; background: #374151; color: white; font-size: 16px; cursor: pointer; }}
    button:hover {{ background: #4b5563; }}
    button.stop {{ background: #b91c1c; }}
    button.primary {{ background: #2563eb; }}
    .row {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
    pre {{ white-space: pre-wrap; word-break: break-word; background: #0b1220; padding: 12px; border-radius: 12px; }}
    input {{ width: 100px; padding: 10px; border-radius: 8px; border: 1px solid #4b5563; background: #111827; color: white; }}
    input.wide {{ width: 260px; }}
    .status {{ font-size: 14px; opacity: 0.95; }}
    .badge {{ display:inline-block; padding:6px 10px; border-radius:999px; font-weight:700; font-size:13px; }}
    .badge.ok {{ background:#14532d; color:#dcfce7; }}
    .badge.bad {{ background:#7f1d1d; color:#fee2e2; }}
    .muted {{ opacity:0.8; font-size:13px; }}
    .meta {{ margin-top:10px; display:grid; gap:8px; }}
  </style>
</head>
<body>
  <div class=\"wrap\">
    <div class=\"card\">
      <h1>Wonder Dash Control Panel</h1>
      <div class=\"status\" id=\"status\">Loading...</div>
      <div class=\"meta\">
        <div id=\"connection_badge\" class=\"badge bad\">DISCONNECTED</div>
        <div class=\"row\">
          <button onclick=\"reconnect()\">Reconnect</button>
          <button onclick=\"refresh()\">Refresh Status</button>
        </div>
        <div class=\"muted\" id=\"last_action_summary\">last_action: loading...</div>
      </div>
    </div>

    <div class=\"card\">
      <h2>Manual Control</h2>
      <div class=\"row\" style=\"margin-bottom:12px\">
        <label>Duration ms <input id=\"duration\" type=\"number\" value=\"1200\" /></label>
        <label>Speed <input id=\"speed\" type=\"number\" value=\"120\" /></label>
      </div>
      <div class=\"grid\">
        <div></div>
        <button class=\"primary\" onclick=\"move('forward')\">Forward</button>
        <div></div>
        <button onclick=\"move('turn_left')\">Turn Left</button>
        <button class=\"stop\" onclick=\"stopNow()\">Stop</button>
        <button onclick=\"move('turn_right')\">Turn Right</button>
        <div></div>
        <button onclick=\"move('backward')\">Backward</button>
        <div></div>
      </div>
    </div>

    <div class=\"card\">
      <h2>Lights</h2>
      <div class=\"row\" style=\"margin-bottom:12px\">
        <label>Neck <input id=\"neck_color\" type=\"text\" value=\"#00aaff\" /></label>
        <label>Left Ear <input id=\"left_ear_color\" type=\"text\" value=\"#ff66aa\" /></label>
        <label>Right Ear <input id=\"right_ear_color\" type=\"text\" value=\"#66ff99\" /></label>
      </div>
      <div class=\"row\">
        <label>Tail <input id=\"tail_brightness\" type=\"number\" value=\"180\" /></label>
        <label>Eyes <input id=\"eye_brightness\" type=\"number\" value=\"180\" /></label>
        <button onclick=\"setLights()\">Apply Lights</button>
      </div>
    </div>

    <div class=\"card\">
      <h2>Look / Head</h2>
      <div class=\"row\">
        <label>Yaw <input id=\"yaw\" type=\"number\" value=\"0\" /></label>
        <label>Pitch <input id=\"pitch\" type=\"number\" value=\"0\" /></label>
        <button onclick=\"look()\">Look</button>
      </div>
    </div>

    <div class=\"card\">
      <h2>Speak</h2>
      <div class=\"row\">
        <label>Text <input class=\"wide\" id=\"speak_text\" type=\"text\" value=\"Hello from Dash\" /></label>
        <button onclick=\"speak()\">Speak</button>
      </div>
    </div>

    <div class=\"card\">
      <h2>Routes</h2>
      <div class=\"row\">{routes}</div>
    </div>

    <div class=\"card\">
      <h2>Wander</h2>
      <div class=\"row\">
        <label>Duration s <input id=\"wander_s\" type=\"number\" value=\"10\" /></label>
        <button onclick=\"wander()\">Start Wander</button>
      </div>
    </div>

    <div class=\"card\">
      <h2>Last Response</h2>
      <pre id=\"output\">Ready.</pre>
    </div>
  </div>

  <script>
    async function api(path, body) {{
      const res = await fetch(path, {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify(body)
      }});
      const text = await res.text();
      document.getElementById('output').textContent = text;
      await refresh();
    }}

    function getDuration() {{ return parseInt(document.getElementById('duration').value || '1200', 10); }}
    function getSpeed() {{ return parseInt(document.getElementById('speed').value || '120', 10); }}
    function getWanderS() {{ return parseInt(document.getElementById('wander_s').value || '10', 10); }}
    function getInt(id, fallback='0') {{ return parseInt(document.getElementById(id).value || fallback, 10); }}
    function getText(id, fallback='') {{ return document.getElementById(id).value || fallback; }}

    async function move(action) {{ await api('/move', {{ action, duration_ms: getDuration(), speed: getSpeed() }}); }}
    async function stopNow() {{ await api('/stop', {{}}); }}
    async function sendRoute(name) {{ await api('/route', {{ name }}); }}
    async function wander() {{ await api('/wander', {{ duration_s: getWanderS() }}); }}
    async function setLights() {{
      await api('/lights', {{
        neck_color: getText('neck_color'),
        left_ear_color: getText('left_ear_color'),
        right_ear_color: getText('right_ear_color'),
        tail_brightness: getInt('tail_brightness', '180'),
        eye_brightness: getInt('eye_brightness', '180')
      }});
    }}
    async function look() {{
      await api('/look', {{
        yaw: getInt('yaw', '0'),
        pitch: getInt('pitch', '0')
      }});
    }}
    async function speak() {{
      await api('/speak', {{ text: getText('speak_text', 'Hello from Dash') }});
    }}
    async function reconnect() {{
      await api('/reconnect', {{}});
    }}

    async function refresh() {{
      const res = await fetch('/health');
      const data = await res.json();
      const connected = !!data.connected;
      document.getElementById('status').textContent = `driver=${{data.driver}} | connected=${{data.connected}} | busy=${{data.busy}} | routes=${{data.routes.join(', ')}} | dash=${{data.dash_address || 'n/a'}}`;
      const badge = document.getElementById('connection_badge');
      badge.textContent = connected ? 'CONNECTED' : 'DISCONNECTED';
      badge.className = `badge ${{connected ? 'ok' : 'bad'}}`;
      const lastAction = data.last_action ? JSON.stringify(data.last_action) : 'none';
      document.getElementById('last_action_summary').textContent = `last_action: ${{lastAction}}`;
    }}

    refresh();
    setInterval(refresh, 3000);
  </script>
</body>
</html>
"""


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
        "gamepad_connected": (time.time() - state.last_gamepad_ping) < 5.0,
    }


@app.post("/reconnect")
async def reconnect() -> Dict[str, Any]:
    if state.busy:
        raise HTTPException(status_code=409, detail="bridge busy")
    state.busy = True
    try:
        await driver.reconnect()
        return {"ok": True, "connected": state.connected, "last_action": state.last_action}
    finally:
        state.busy = False


@app.post("/stop")
async def stop() -> Dict[str, Any]:
    await driver.stop()
    state.busy = False
    return {"ok": True, "stopped": True}


@app.post("/move")
async def move(req: MoveRequest) -> Dict[str, Any]:
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
    if state.busy:
        raise HTTPException(status_code=409, detail="bridge busy")
    state.busy = True
    try:
        return await driver.lights(
            neck_color=req.neck_color,
            left_ear_color=req.left_ear_color,
            right_ear_color=req.right_ear_color,
            tail_brightness=req.tail_brightness,
            eye_brightness=req.eye_brightness,
        )
    finally:
        state.busy = False


@app.post("/look")
async def look(req: LookRequest) -> Dict[str, Any]:
    if state.busy:
        raise HTTPException(status_code=409, detail="bridge busy")
    state.busy = True
    try:
        return await driver.look(yaw=req.yaw, pitch=req.pitch)
    finally:
        state.busy = False


@app.post("/speak")
async def speak(req: SpeakRequest) -> Dict[str, Any]:
    if state.busy:
        raise HTTPException(status_code=409, detail="bridge busy")
    state.busy = True
    try:
        return await driver.speak(req.text)
    finally:
        state.busy = False


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
            duration_ms = random.choice([400, 700, 1000, 1400])
            speed = random.choice([120, 140, 160])
            result = await driver.move(action, duration_ms, speed)
            executed.append(result)
            await asyncio.sleep(0.2)
        await driver.stop()
        return {"ok": True, "mode": "wander", "count": len(executed), "steps": executed}
    finally:
        state.busy = False

@app.post("/gamepad/ping")
async def gamepad_ping() -> Dict[str, Any]:
    state.last_gamepad_ping = time.time()
    return {"ok": True}
