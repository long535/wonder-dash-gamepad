from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional


class BaseDashDriver:
    name = "base"

    async def connect(self) -> None:
        raise NotImplementedError

    async def reconnect(self) -> None:
        await self.connect()

    async def stop(self) -> None:
        raise NotImplementedError

    async def drive_start(self, action: str, speed: int) -> Dict[str, Any]:
        """Start continuous driving (non-blocking). Returns immediately."""
        raise NotImplementedError

    async def move(self, action: str, duration_ms: int, speed: int) -> Dict[str, Any]:
        """Legacy blocking move: drive for duration then stop."""
        raise NotImplementedError

    async def lights(
        self,
        neck_color: Optional[str] = None,
        left_ear_color: Optional[str] = None,
        right_ear_color: Optional[str] = None,
        tail_brightness: Optional[int] = None,
        eye_brightness: Optional[int] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    async def look(self, yaw: Optional[int] = None, pitch: Optional[int] = None) -> Dict[str, Any]:
        raise NotImplementedError

    async def speak(self, text: str) -> Dict[str, Any]:
        raise NotImplementedError

    def get_sensors(self) -> Dict[str, Any]:
        """Return current sensor readings (non-blocking)."""
        return {}


class MockDashDriver(BaseDashDriver):
    name = "mock"

    def __init__(self, state: Any) -> None:
        self.state = state
        self._driving = False
        self._driving_action: Optional[str] = None

    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        self.state.connected = True

    async def stop(self) -> None:
        self._driving = False
        self._driving_action = None
        self.state.last_action = {"action": "stop", "driver": self.name}

    async def drive_start(self, action: str, speed: int) -> Dict[str, Any]:
        self._driving = True
        self._driving_action = action
        self.state.last_action = {
            "action": action,
            "mode": "continuous",
            "speed": speed,
            "driver": self.name,
        }
        return {
            "ok": True,
            "driver": self.name,
            "action": action,
            "mode": "continuous",
            "speed": speed,
        }

    async def move(self, action: str, duration_ms: int, speed: int) -> Dict[str, Any]:
        self.state.last_action = {
            "action": action,
            "duration_ms": duration_ms,
            "speed": speed,
            "driver": self.name,
        }
        await asyncio.sleep(duration_ms / 1000)
        return {
            "ok": True,
            "driver": self.name,
            "action": action,
            "duration_ms": duration_ms,
            "speed": speed,
        }

    async def lights(
        self,
        neck_color: Optional[str] = None,
        left_ear_color: Optional[str] = None,
        right_ear_color: Optional[str] = None,
        tail_brightness: Optional[int] = None,
        eye_brightness: Optional[int] = None,
    ) -> Dict[str, Any]:
        payload = {
            "ok": True,
            "driver": self.name,
            "action": "lights",
            "neck_color": neck_color,
            "left_ear_color": left_ear_color,
            "right_ear_color": right_ear_color,
            "tail_brightness": tail_brightness,
            "eye_brightness": eye_brightness,
        }
        self.state.last_action = payload
        return payload

    async def look(self, yaw: Optional[int] = None, pitch: Optional[int] = None) -> Dict[str, Any]:
        payload = {
            "ok": True,
            "driver": self.name,
            "action": "look",
            "yaw": yaw,
            "pitch": pitch,
        }
        self.state.last_action = payload
        return payload

    async def speak(self, text: str) -> Dict[str, Any]:
        payload = {
            "ok": True,
            "driver": self.name,
            "action": "speak",
            "text": text,
        }
        self.state.last_action = payload
        return payload

    def get_sensors(self) -> Dict[str, Any]:
        return {
            "prox_left": 0,
            "prox_right": 0,
            "prox_rear": 0,
            "moving": self._driving,
            "picked_up": False,
            "yaw": 0,
            "pitch": 0,
            "roll": 0,
        }


class RealDashDriver(BaseDashDriver):
    name = "real-dash"

    def __init__(self, state: Any, address: Optional[str] = None) -> None:
        self.state = state
        self.address = address
        self.robot = None
        # Obstacle avoidance
        self._obstacle_task: Optional[asyncio.Task] = None
        self._obstacle_enabled = True
        self._obstacle_threshold = 15  # proximity value above which = obstacle detected
        self._driving = False
        self._driving_action: Optional[str] = None
        self._drive_task: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()

    async def _bluetoothctl(self, *commands: str, timeout: float = 10.0) -> str:
        script = "\n".join(commands) + "\n"
        proc = await asyncio.create_subprocess_exec(
            'bluetoothctl',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(script.encode()), timeout=timeout)
        return stdout.decode(errors='ignore')

    async def _ble_warmup_scan(self, seconds: int = 6) -> None:
        try:
            await self._bluetoothctl(f'scan on', f'info {self.address}', f'scan off', timeout=max(8.0, seconds + 4.0))
        except Exception:
            pass

    async def _disconnect_robot(self) -> None:
        if self._obstacle_task and not self._obstacle_task.done():
            self._obstacle_task.cancel()
            self._obstacle_task = None
        if self._drive_task and not self._drive_task.done():
            self._drive_task.cancel()
        self._drive_task = None
        if self.robot and hasattr(self.robot, 'disconnect'):
            try:
                await self.robot.disconnect()
            except Exception:
                pass
        self.robot = None
        self.state.connected = False
        self._driving = False
        self._driving_action = None

    async def connect(self) -> None:
        async with self._connect_lock:
            if not self.address:
                self.state.connected = False
                self.robot = None
                self.state.last_action = {
                    "action": "connect_failed",
                    "driver": self.name,
                    "reason": "missing_address",
                    "at": time.time(),
                }
                return

            from dash.robot import DashRobot

            last_exc = None
            for attempt in range(1, 4):
                await self._disconnect_robot()
                if attempt > 1:
                    await self._ble_warmup_scan(6)
                    await asyncio.sleep(1.0)
                self.robot = DashRobot(self.address)
                try:
                    await asyncio.wait_for(self.robot.connect(), timeout=12.0)
                    self.state.connected = True
                    self.state.last_action = {
                        "action": "connected",
                        "driver": self.name,
                        "address": self.address,
                        "attempt": attempt,
                        "at": time.time(),
                    }
                    self._start_obstacle_monitor()
                    return
                except Exception as exc:
                    last_exc = exc
                    msg = f"{type(exc).__name__}: {exc}"
                    self.state.connected = False
                    self.robot = None
                    self.state.last_action = {
                        "action": "connect_retrying" if attempt < 3 else "connect_failed",
                        "driver": self.name,
                        "address": self.address,
                        "attempt": attempt,
                        "error": msg,
                        "at": time.time(),
                    }
                    if 'InProgress' in msg or 'not found' in msg.lower() or 'not connectable' in msg.lower() or 'TimeoutError' in msg:
                        continue
                    raise
            raise last_exc

    def _start_obstacle_monitor(self) -> None:
        if self._obstacle_task is None or self._obstacle_task.done():
            self._obstacle_task = asyncio.ensure_future(self._obstacle_monitor_loop())

    async def _obstacle_monitor_loop(self) -> None:
        """Background task: if driving forward and proximity sensor detects obstacle, auto-stop."""
        while True:
            try:
                await asyncio.sleep(0.15)  # check ~7 times/sec
                if not self._obstacle_enabled or not self._driving or not self.robot:
                    continue
                if not self.state.connected:
                    continue

                sensors = self.get_sensors()
                prox_left = sensors.get("prox_left", 0)
                prox_right = sensors.get("prox_right", 0)
                prox_rear = sensors.get("prox_rear", 0)

                obstacle_front = (
                    self._driving_action in ("forward",)
                    and (prox_left > self._obstacle_threshold or prox_right > self._obstacle_threshold)
                )
                obstacle_rear = (
                    self._driving_action in ("backward",)
                    and prox_rear > self._obstacle_threshold
                )

                if obstacle_front or obstacle_rear:
                    # Auto-stop
                    try:
                        await self.robot.stop()
                    except Exception:
                        pass
                    self._driving = False
                    direction = "front" if obstacle_front else "rear"
                    self.state.last_action = {
                        "action": "obstacle_stop",
                        "driver": self.name,
                        "direction": direction,
                        "prox_left": prox_left,
                        "prox_right": prox_right,
                        "prox_rear": prox_rear,
                        "at": time.time(),
                    }
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1)

    async def reconnect(self) -> None:
        await self._disconnect_robot()
        await self._ble_warmup_scan(6)
        await asyncio.sleep(1.0)
        await self.connect()

    async def _ensure_connected(self) -> None:
        if self.robot is None or not self.state.connected:
            await self.connect()

    async def stop(self) -> None:
        self._driving = False
        self._driving_action = None
        if self._drive_task and not self._drive_task.done():
            self._drive_task.cancel()
        self._drive_task = None
        try:
            await self._ensure_connected()
            if self.robot and hasattr(self.robot, "stop"):
                await self.robot.stop()
            self.state.last_action = {"action": "stop", "driver": self.name, "at": time.time()}
        except Exception as exc:
            self.state.connected = False
            self.robot = None
            self.state.last_action = {
                "action": "stop_failed",
                "driver": self.name,
                "error": f"{type(exc).__name__}: {exc}",
                "at": time.time(),
            }
            raise

    async def drive_start(self, action: str, speed: int) -> Dict[str, Any]:
        """Non-blocking: start driving and return immediately. Motor keeps running until stop()."""
        try:
            await self._ensure_connected()
            if not self.robot:
                raise RuntimeError("Dash robot not connected")

            drive_speed = max(60, min(300, int(speed)))

            if self._drive_task and not self._drive_task.done():
                self._drive_task.cancel()
            self._drive_task = None

            if action == "forward":
                await self.robot.drive(abs(drive_speed))
            elif action == "backward":
                seconds = 30.0
                speed_mmps = max(60, abs(drive_speed))
                distance_mm = -int(speed_mmps * seconds)

                async def run_backward():
                    try:
                        if self.robot and hasattr(self.robot, "move"):
                            await self.robot.move(distance_mm=distance_mm, speed_mmps=speed_mmps, no_turn=True)
                        elif self.robot:
                            await self.robot.drive(-abs(drive_speed))
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass

                self._drive_task = asyncio.create_task(run_backward())
            elif action == "turn_left":
                await self.robot.spin(-abs(drive_speed))
            elif action == "turn_right":
                await self.robot.spin(abs(drive_speed))
            elif action == "stop":
                await self.stop()
                return {"ok": True, "driver": self.name, "action": "stop", "mode": "continuous"}
            else:
                raise ValueError(f"unsupported action: {action}")

            self._driving = True
            self._driving_action = action
            self.state.last_action = {
                "action": action,
                "mode": "continuous",
                "speed": drive_speed,
                "driver": self.name,
                "at": time.time(),
            }
            return {
                "ok": True,
                "driver": self.name,
                "action": action,
                "mode": "continuous",
                "speed": drive_speed,
            }
        except Exception as exc:
            self.state.connected = False
            self.robot = None
            self._driving = False
            self.state.last_action = {
                "action": "drive_start_failed",
                "requested_action": action,
                "speed": speed,
                "driver": self.name,
                "error": f"{type(exc).__name__}: {exc}",
                "at": time.time(),
            }
            raise

    async def move(self, action: str, duration_ms: int, speed: int) -> Dict[str, Any]:
        """Legacy blocking move (used by routes/wander)."""
        try:
            await self._ensure_connected()
            if not self.robot:
                raise RuntimeError("Dash robot not connected")

            drive_speed = max(60, min(300, int(speed)))
            if action == "forward":
                self._driving = True
                self._driving_action = "forward"
                await self.robot.drive(abs(drive_speed))
                await asyncio.sleep(duration_ms / 1000)
                await self.robot.stop()
                self._driving = False
            elif action == "backward":
                self._driving = True
                self._driving_action = "backward"
                seconds = max(0.1, duration_ms / 1000)
                speed_mmps = max(60, abs(drive_speed))
                distance_mm = -int(speed_mmps * seconds)
                if hasattr(self.robot, "move"):
                    await self.robot.move(distance_mm=distance_mm, speed_mmps=speed_mmps, no_turn=True)
                else:
                    await self.robot.drive(-abs(drive_speed))
                    await asyncio.sleep(seconds)
                    await self.robot.stop()
                self._driving = False
            elif action == "turn_left":
                seconds = max(0.1, duration_ms / 1000)
                speed_ratio = abs(drive_speed) / 200.0
                degrees = -int(seconds * 171.9 * speed_ratio)
                if hasattr(self.robot, "_get_move_byte_array") and hasattr(self.robot, "command"):
                    byte_array = self.robot._get_move_byte_array(distance_mm=0, degrees=degrees, seconds=seconds)
                    await self.robot.command("move", byte_array)
                    await asyncio.sleep(seconds)
                else:
                    await self.robot.spin(-abs(drive_speed))
                    await asyncio.sleep(seconds)
                    await self.robot.stop()
            elif action == "turn_right":
                seconds = max(0.1, duration_ms / 1000)
                speed_ratio = abs(drive_speed) / 200.0
                degrees = int(seconds * 171.9 * speed_ratio)
                if hasattr(self.robot, "_get_move_byte_array") and hasattr(self.robot, "command"):
                    byte_array = self.robot._get_move_byte_array(distance_mm=0, degrees=degrees, seconds=seconds)
                    await self.robot.command("move", byte_array)
                    await asyncio.sleep(seconds)
                else:
                    await self.robot.spin(abs(drive_speed))
                    await asyncio.sleep(seconds)
                    await self.robot.stop()
            elif action == "stop":
                await self.robot.stop()
                self._driving = False
            else:
                raise ValueError(f"unsupported real-dash action: {action}")

            self._driving_action = None
            self.state.last_action = {
                "action": action,
                "duration_ms": duration_ms,
                "speed": drive_speed,
                "driver": self.name,
                "at": time.time(),
            }
            return {
                "ok": True,
                "driver": self.name,
                "action": action,
                "duration_ms": duration_ms,
                "speed": drive_speed,
            }
        except Exception as exc:
            self.state.connected = False
            self.robot = None
            self._driving = False
            self.state.last_action = {
                "action": "move_failed",
                "requested_action": action,
                "duration_ms": duration_ms,
                "speed": speed,
                "driver": self.name,
                "error": f"{type(exc).__name__}: {exc}",
                "at": time.time(),
            }
            raise

    async def lights(
        self,
        neck_color: Optional[str] = None,
        left_ear_color: Optional[str] = None,
        right_ear_color: Optional[str] = None,
        tail_brightness: Optional[int] = None,
        eye_brightness: Optional[int] = None,
    ) -> Dict[str, Any]:
        try:
            await self._ensure_connected()
            if not self.robot:
                raise RuntimeError("Dash robot not connected")

            if neck_color is not None and hasattr(self.robot, "neck_color"):
                await self.robot.neck_color(neck_color)
            if left_ear_color is not None and hasattr(self.robot, "left_ear_color"):
                await self.robot.left_ear_color(left_ear_color)
            if right_ear_color is not None and hasattr(self.robot, "right_ear_color"):
                await self.robot.right_ear_color(right_ear_color)
            if tail_brightness is not None and hasattr(self.robot, "tail_brightness"):
                await self.robot.tail_brightness(int(tail_brightness))
            if eye_brightness is not None and hasattr(self.robot, "eye_brightness"):
                await self.robot.eye_brightness(int(eye_brightness))

            payload = {
                "ok": True,
                "driver": self.name,
                "action": "lights",
                "neck_color": neck_color,
                "left_ear_color": left_ear_color,
                "right_ear_color": right_ear_color,
                "tail_brightness": tail_brightness,
                "eye_brightness": eye_brightness,
                "at": time.time(),
            }
            self.state.last_action = payload
            return payload
        except Exception as exc:
            self.state.connected = False
            self.robot = None
            self.state.last_action = {
                "action": "lights_failed",
                "driver": self.name,
                "error": f"{type(exc).__name__}: {exc}",
                "at": time.time(),
            }
            raise

    async def look(self, yaw: Optional[int] = None, pitch: Optional[int] = None) -> Dict[str, Any]:
        try:
            await self._ensure_connected()
            if not self.robot:
                raise RuntimeError("Dash robot not connected")

            if yaw is not None and hasattr(self.robot, "head_yaw"):
                await self.robot.head_yaw(int(yaw))
            if pitch is not None and hasattr(self.robot, "head_pitch"):
                await self.robot.head_pitch(int(pitch))

            payload = {
                "ok": True,
                "driver": self.name,
                "action": "look",
                "yaw": yaw,
                "pitch": pitch,
                "at": time.time(),
            }
            self.state.last_action = payload
            return payload
        except Exception as exc:
            self.state.connected = False
            self.robot = None
            self.state.last_action = {
                "action": "look_failed",
                "driver": self.name,
                "error": f"{type(exc).__name__}: {exc}",
                "at": time.time(),
            }
            raise

    async def speak(self, text: str) -> Dict[str, Any]:
        try:
            await self._ensure_connected()
            if not self.robot:
                raise RuntimeError("Dash robot not connected")

            if hasattr(self.robot, "say"):
                await self.robot.say(text)

            payload = {
                "ok": True,
                "driver": self.name,
                "action": "speak",
                "text": text,
                "at": time.time(),
            }
            self.state.last_action = payload
            return payload
        except Exception as exc:
            self.state.connected = False
            self.robot = None
            self.state.last_action = {
                "action": "speak_failed",
                "driver": self.name,
                "error": f"{type(exc).__name__}: {exc}",
                "at": time.time(),
            }
            raise

    def get_sensors(self) -> Dict[str, Any]:
        """Return latest sensor readings from the robot's BLE notification stream."""
        if not self.robot or not hasattr(self.robot, "sensor_state"):
            return {}
        ss = self.robot.sensor_state
        return {
            "prox_left": ss.get("prox_left", 0),
            "prox_right": ss.get("prox_right", 0),
            "prox_rear": ss.get("prox_rear", 0),
            "yaw": ss.get("yaw", 0),
            "pitch": ss.get("pitch", 0),
            "roll": ss.get("roll", 0),
            "moving": ss.get("moving", False),
            "picked_up": ss.get("picked_up", False),
            "hit": ss.get("hit", False),
            "left_wheel": ss.get("left_wheel", 0),
            "right_wheel": ss.get("right_wheel", 0),
            "head_pitch": ss.get("head_pitch", 0),
            "head_yaw": ss.get("head_yaw", 0),
            "wheel_distance": ss.get("wheel_distance", 0),
            "mic_level": ss.get("mic_level", 0),
            "clap": ss.get("clap", False),
            "sound_direction": ss.get("sound_direction", 0),
        }

    async def set_obstacle_avoidance(self, enabled: bool, threshold: int = 15) -> Dict[str, Any]:
        self._obstacle_enabled = enabled
        self._obstacle_threshold = threshold
        return {
            "ok": True,
            "obstacle_avoidance": enabled,
            "threshold": threshold,
        }


def build_driver(state: Any, mode: str = "mock", address: Optional[str] = None) -> BaseDashDriver:
    if mode == "real":
        return RealDashDriver(state=state, address=address)
    return MockDashDriver(state=state)
