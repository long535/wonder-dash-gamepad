# Wonder Dash as OpenClaw Body v1

## Goal
Let OpenClaw issue high-level commands like:
- `go to living_room`
- `wander around`
- `stop`

and have a Raspberry Pi bridge translate them into Wonder Dash movement commands.

## Architecture
- **OpenClaw**: intent understanding, route selection, safety policy
- **Bridge API**: small HTTP service on Raspberry Pi
- **Dash driver**: BLE connection + primitive actions
- **Routes config**: named locations and prerecorded action sequences

## v1 Scope
v1 does **not** attempt SLAM or precise indoor localization.
It uses:
- named places
- prerecorded routes
- simple movement primitives
- stop / timeout safety

## Command Flow
1. User says `去 living room`
2. OpenClaw maps that to `route=living_room`
3. Pi bridge loads the route from `config/routes.example.yaml`
4. Pi executes movement primitives on Dash
5. Pi returns status/events

## Safety Rules
- hard stop endpoint
- per-command timeout
- auto-stop on disconnect
- reject concurrent route execution
- max wander duration
- optional quiet hours

## API Sketch
### `GET /health`
Returns service and BLE status.

### `POST /stop`
Immediate stop.

### `POST /move`
```json
{ "action": "forward", "duration_ms": 1200, "speed": 50 }
```

### `POST /route`
```json
{ "name": "living_room" }
```

### `POST /wander`
```json
{ "duration_s": 60 }
```

## Route Model
Routes are sequences of primitive steps.
Example:
```yaml
routes:
  living_room:
    - action: forward
      duration_ms: 3500
      speed: 50
    - action: turn_right
      duration_ms: 900
      speed: 35
    - action: forward
      duration_ms: 2400
      speed: 50
```

## Milestones
1. Confirm BLE library/protocol for Dash
2. Bring up Pi bridge service
3. Implement fake driver for dry-run testing
4. Replace fake driver with real Dash driver
5. Calibrate named routes in the home
6. Add wander mode + recovery rules

## Current Status
- Raspberry Pi reachable over SSH
- Bluetooth stack enabled and unblocked
- Python/BLE build dependencies installed
- Bridge skeleton prepared in this workspace
- Driver adapter split into `mock` and placeholder `real` modes
- Candidate BLE library path validated: `mewmix/bleak-dash` works best so far on Pi when installed with a newer `bleak`

## Pre-Hardware Next Steps
Even without the robot physically present, these are worth doing:
1. Keep the bridge in `mock` mode for API and route testing
2. Refine route schema and safety policy
3. Add a real-driver adapter layer so hardware hookup is a drop-in change
4. Prepare calibration notes for named places like `living_room`, `dock`, `hallway`

## DAO1 Device Profile
See:
- `config/device.dao1.yaml`
- `DAO1-ARRIVAL-CHECKLIST.md`

These capture the known hardware identity, current assumptions, safety defaults, and the first-day bring-up sequence.

## Suggested Next Step
When Dash DAO1 is physically present:
1. scan for the robot MAC address
2. set `WONDER_DASH_DRIVER=real`
3. set `WONDER_DASH_ADDRESS=<mac>`
4. verify harmless commands first (lights/sound), then movement
5. only then calibrate named routes like `living_room`
