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

    async def move(self, action: str, duration_ms: int, speed: int) -> Dict[str, Any]:
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


class MockDashDriver(BaseDashDriver):
    name = "mock"

    def __init__(self, state: Any) -> None:
        self.state = state

    async def connect(self) -> None:
        await asyncio.sleep(0.1)
        self.state.connected = True

    async def stop(self) -> None:
        self.state.last_action = {"action": "stop", "driver": self.name}

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


class RealDashDriver(BaseDashDriver):
    name = "real-dash"

    def __init__(self, state: Any, address: Optional[str] = None) -> None:
        self.state = state
        self.address = address
        self.robot = None

    async def connect(self) -> None:
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

        self.robot = DashRobot(self.address)
        try:
            await self.robot.connect()
        except Exception as exc:
            self.state.connected = False
            self.robot = None
            self.state.last_action = {
                "action": "connect_failed",
                "driver": self.name,
                "address": self.address,
                "error": f"{type(exc).__name__}: {exc}",
                "at": time.time(),
            }
            raise
        self.state.connected = True
        self.state.last_action = {
            "action": "connected",
            "driver": self.name,
            "address": self.address,
            "at": time.time(),
        }

    async def reconnect(self) -> None:
        if self.robot and hasattr(self.robot, "disconnect"):
            try:
                await self.robot.disconnect()
            except Exception:
                pass
        self.robot = None
        self.state.connected = False
        await self.connect()

    async def _ensure_connected(self) -> None:
        if self.robot is None or not self.state.connected:
            await self.connect()

    async def stop(self) -> None:
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

    async def move(self, action: str, duration_ms: int, speed: int) -> Dict[str, Any]:
        try:
            await self._ensure_connected()
            if not self.robot:
                raise RuntimeError("Dash robot not connected")

            drive_speed = max(60, min(300, int(speed)))
            if action == "forward":
                await self.robot.drive(abs(drive_speed))
                await asyncio.sleep(duration_ms / 1000)
                await self.robot.stop()
            elif action == "backward":
                seconds = max(0.1, duration_ms / 1000)
                speed_mmps = max(60, abs(drive_speed))
                distance_mm = -int(speed_mmps * seconds)
                if hasattr(self.robot, "move"):
                    await self.robot.move(distance_mm=distance_mm, speed_mmps=speed_mmps, no_turn=True)
                else:
                    await self.robot.drive(-abs(drive_speed))
                    await asyncio.sleep(seconds)
                    await self.robot.stop()
            elif action == "turn_left":
                seconds = max(0.1, duration_ms / 1000)
                if hasattr(self.robot, "spin"):
                    await self.robot.spin(-abs(drive_speed))
                    await asyncio.sleep(seconds)
                    await self.robot.stop()
                elif hasattr(self.robot, "turn"):
                    await self.robot.turn(-90) # default to -90 if only turn is supported
            elif action == "turn_right":
                seconds = max(0.1, duration_ms / 1000)
                if hasattr(self.robot, "spin"):
                    await self.robot.spin(abs(drive_speed))
                    await asyncio.sleep(seconds)
                    await self.robot.stop()
                elif hasattr(self.robot, "turn"):
                    await self.robot.turn(90)
            elif action == "stop":
                await self.robot.stop()
            else:
                raise ValueError(f"unsupported real-dash action: {action}")

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


def build_driver(state: Any, mode: str = "mock", address: Optional[str] = None) -> BaseDashDriver:
    if mode == "real":
        return RealDashDriver(state=state, address=address)
    return MockDashDriver(state=state)
