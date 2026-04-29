#!/usr/bin/env python3
"""Execute canonical NAO action plans, with a mock mode for local testing."""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from typing import Any, Dict, Iterable, List, Tuple

import rclpy
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import SetBool

try:
    from geometry_msgs.msg import Point
except Exception:  # pragma: no cover - only relevant if ROS geometry_msgs is missing
    Point = None

try:
    from naoqi_utilities_msgs.msg import LedParameters
    from naoqi_utilities_msgs.srv import GoToPosture, MoveTo, PlayAnimation, PointAt, Say

    NAOQI_MSGS_AVAILABLE = True
except Exception:  # pragma: no cover - renderer can run in mock mode without these msgs
    LedParameters = None
    GoToPosture = MoveTo = PlayAnimation = PointAt = Say = None
    NAOQI_MSGS_AVAILABLE = False


COLOR_NAMES = {
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "orange": (255, 165, 0),
    "purple": (128, 0, 128),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "blanco": (255, 255, 255),
    "rojo": (255, 0, 0),
    "verde": (0, 255, 0),
    "azul": (0, 0, 255),
    "amarillo": (255, 255, 0),
    "naranja": (255, 165, 0),
    "morado": (128, 0, 128),
}


def _bool_from_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


class BehaviorRendererNode(Node):
    """Consumes action plans and translates them to NAOqi bridge calls."""

    def __init__(self) -> None:
        super().__init__("nao_behavior_renderer")

        self.declare_parameter("plan_topic", "/nao_pedagogical/plan")
        self.declare_parameter("legacy_plan_topic", "/llm/plan")
        self.declare_parameter("listen_legacy_plan", True)
        self.declare_parameter("mock_mode", _bool_from_env("NAO_MOCK_MODE", False))
        self.declare_parameter("service_timeout_sec", 1.0)
        self.declare_parameter("default_language", "Spanish")

        self.plan_topic = self.get_parameter("plan_topic").get_parameter_value().string_value
        self.legacy_plan_topic = self.get_parameter("legacy_plan_topic").get_parameter_value().string_value
        self.listen_legacy_plan = bool(self.get_parameter("listen_legacy_plan").value)
        self.mock_mode = _coerce_bool(self.get_parameter("mock_mode").value) or not NAOQI_MSGS_AVAILABLE
        self.service_timeout_sec = float(self.get_parameter("service_timeout_sec").value)
        self.default_language = self.get_parameter("default_language").get_parameter_value().string_value

        self.create_subscription(String, self.plan_topic, self._on_plan, 10)
        if self.listen_legacy_plan and self.legacy_plan_topic != self.plan_topic:
            self.create_subscription(String, self.legacy_plan_topic, self._on_plan, 10)

        self.pub_state = self.create_publisher(String, "/interaction/state", 10)
        self.pub_leds = self.create_publisher(LedParameters, "/set_leds", 10) if NAOQI_MSGS_AVAILABLE else None

        self.cli_say = self.create_client(Say, "/naoqi_speech_node/say") if not self.mock_mode else None
        self.cli_go_to_posture = (
            self.create_client(GoToPosture, "/naoqi_manipulation_node/go_to_posture") if not self.mock_mode else None
        )
        self.cli_play_animation = (
            self.create_client(PlayAnimation, "/naoqi_manipulation_node/play_animation") if not self.mock_mode else None
        )
        self.cli_move_to = self.create_client(MoveTo, "/naoqi_navigation_node/move_to") if not self.mock_mode else None
        self.cli_point_at = self.create_client(PointAt, "/naoqi_perception_node/point_at") if not self.mock_mode else None
        self.cli_toggle_awareness = (
            self.create_client(SetBool, "/naoqi_miscellaneous_node/toggle_awareness") if not self.mock_mode else None
        )

        self.current_plan_id: Any = None
        self.current_posture: str | None = None
        self.action_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self.worker_thread = threading.Thread(target=self._process_actions, daemon=True)
        self.worker_thread.start()

        mode = "mock" if self.mock_mode else "naoqi"
        self.get_logger().info(f"Behavior renderer ready mode={mode} plan_topic={self.plan_topic}")

    def _on_plan(self, msg: String) -> None:
        try:
            plan = json.loads(msg.data)
            if not isinstance(plan, dict):
                raise ValueError("plan is not a JSON object")
        except Exception as exc:
            self.get_logger().error(f"Invalid plan JSON: {exc}")
            return

        plan_id = plan.get("meta", {}).get("plan_id")
        if plan_id and plan_id != self.current_plan_id:
            self._clear_queue()
            self.current_plan_id = plan_id

        actions = plan.get("actions", [])
        if not isinstance(actions, list):
            self.get_logger().warning("Plan without actions list; ignored.")
            return

        normalized = self._normalize_actions(actions)
        for action in normalized:
            self.action_queue.put(action)
        self.pub_state.publish(String(data="ROBOT_PLAN_RUNNING"))
        self.get_logger().info(f"Queued {len(normalized)} action(s) plan_id={self.current_plan_id}")

    def _clear_queue(self) -> None:
        while not self.action_queue.empty():
            try:
                self.action_queue.get_nowait()
                self.action_queue.task_done()
            except Exception:
                break

    def _normalize_actions(self, actions: Iterable[Any]) -> List[Dict[str, Any]]:
        clean: List[Dict[str, Any]] = []
        previous: Dict[str, Any] | None = None
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("type", "")).lower().strip()
            if not action_type:
                continue
            item = dict(action)
            item["type"] = action_type
            if item == previous:
                continue
            clean.append(item)
            previous = item
        return self._ensure_postures(clean)

    def _ensure_postures(self, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        posture_shadow = self.current_posture
        for action in actions:
            required = self._required_posture(action)
            if required and required != posture_shadow:
                out.append({"type": "go_to_posture", "name": required})
                posture_shadow = required
            out.append(action)
            if action.get("type") == "go_to_posture":
                posture_shadow = action.get("name")
        return out

    def _required_posture(self, action: Dict[str, Any]) -> str | None:
        action_type = action.get("type")
        if action_type == "move_to":
            return "Stand"
        if action_type == "point_at":
            return "Stand"
        if action_type == "play_animation":
            name = str(action.get("animation_name", ""))
            if name.startswith("Stand/"):
                return "Stand"
            if name.startswith("Sit/"):
                return "Sit"
            if name.startswith("Rest/"):
                return "Rest"
            match = re.search(r"(^|/)(stand|sit|rest)(/|$)", name, re.IGNORECASE)
            if match:
                token = match.group(2).lower()
                return "Stand" if token == "stand" else ("Sit" if token == "sit" else "Rest")
        return None

    def _process_actions(self) -> None:
        while rclpy.ok():
            action = self.action_queue.get()
            try:
                self._execute_action(action)
            except Exception as exc:
                self.get_logger().error(f"Action failed {action}: {exc}")
            finally:
                self.action_queue.task_done()

            if self.action_queue.empty() and self.current_plan_id is not None:
                self.current_plan_id = None
                self.pub_state.publish(String(data="ROBOT_PLAN_DONE"))
                self._set_leds("FaceLeds", "purple", 0.5)

    def _execute_action(self, action: Dict[str, Any]) -> None:
        action_type = action.get("type")
        if action_type == "say":
            self._say(str(action.get("text", "")), str(action.get("language", "") or self.default_language), bool(action.get("animated", False)))
            return
        if action_type in ("set_leds", "leds", "eyes", "set_eyes_color"):
            self._set_leds(str(action.get("name", "FaceLeds")), action.get("color", action), float(action.get("duration", 0.5)))
            return
        if action_type == "go_to_posture":
            self._go_to_posture(str(action.get("name", "Stand")))
            return
        if action_type == "play_animation":
            self._play_animation(str(action.get("animation_name", "")))
            return
        if action_type == "move_to":
            self._move_to(float(action.get("x", 0.0)), float(action.get("y", 0.0)), float(action.get("theta", 0.0)))
            return
        if action_type == "point_at":
            self._point_at(action)
            return
        if action_type == "pause":
            seconds = max(0.0, min(10.0, float(action.get("seconds", 1.0))))
            self.get_logger().info(f"Pause {seconds:.2f}s")
            time.sleep(seconds)
            return
        self.get_logger().warning(f"Unsupported action type={action_type}")

    def _say(self, text: str, language: str, animated: bool) -> None:
        text = " ".join(text.split())
        if not text:
            return
        if self.mock_mode or self.cli_say is None:
            self.get_logger().info(f"[MOCK] SAY lang={language} animated={animated}: {text}")
            time.sleep(min(3.0, max(0.8, len(text) / 22.0)))
            return
        self.get_logger().info(f"[ROBOT_SAY] lang={language} animated={animated}: {text}")
        req = Say.Request()
        req.text = text
        req.language = self._normalize_language(language)
        req.animated = animated
        req.asynchronous = False
        self._call_service(self.cli_say, req, "SAY")

    def _set_leds(self, name: str, color: Any, duration: float) -> None:
        r, g, b = self._parse_color(color)
        duration = max(0.05, min(2.0, duration))
        if self.mock_mode or self.pub_leds is None:
            self.get_logger().info(f"[MOCK] LEDS {name} rgb=({r},{g},{b}) duration={duration:.2f}s")
            return
        msg = LedParameters()
        msg.name = name
        msg.red = r
        msg.green = g
        msg.blue = b
        msg.duration = duration
        self.pub_leds.publish(msg)

    def _go_to_posture(self, posture: str) -> None:
        posture = posture.strip() or "Stand"
        if self.current_posture == posture:
            return
        if self.mock_mode or self.cli_go_to_posture is None:
            self.get_logger().info(f"[MOCK] GO_TO_POSTURE {posture}")
            self.current_posture = posture
            time.sleep(0.2)
            return
        req = GoToPosture.Request()
        req.posture_name = posture
        self._call_service(self.cli_go_to_posture, req, f"GO_TO_POSTURE {posture}")
        self.current_posture = posture

    def _play_animation(self, animation_name: str) -> None:
        animation_name = animation_name.strip()
        if not animation_name:
            return
        if self.mock_mode or self.cli_play_animation is None:
            self.get_logger().info(f"[MOCK] PLAY_ANIMATION {animation_name}")
            time.sleep(0.8)
            return
        req = PlayAnimation.Request()
        req.animation_name = animation_name
        self._call_service(self.cli_play_animation, req, f"PLAY_ANIMATION {animation_name}")

    def _move_to(self, x: float, y: float, theta: float) -> None:
        if self.mock_mode or self.cli_move_to is None:
            self.get_logger().info(f"[MOCK] MOVE_TO x={x:.2f} y={y:.2f} theta={theta:.2f}")
            time.sleep(0.5)
            return
        req = MoveTo.Request()
        req.x_coordinate = x
        req.y_coordinate = y
        req.theta_coordinate = theta
        self._call_service(self.cli_move_to, req, f"MOVE_TO x={x:.2f} y={y:.2f} theta={theta:.2f}")

    def _point_at(self, action: Dict[str, Any]) -> None:
        if self.mock_mode or self.cli_point_at is None or Point is None:
            self.get_logger().info(f"[MOCK] POINT_AT {action}")
            time.sleep(0.4)
            return
        req = PointAt.Request()
        req.effector_name = str(action.get("effector", "RArm"))
        req.point = Point(x=float(action.get("x", 0.3)), y=float(action.get("y", 0.0)), z=float(action.get("z", 0.9)))
        req.frame = {"TORSO": 0, "WORLD": 1, "ROBOT": 2}.get(str(action.get("frame", "TORSO")).upper(), 0)
        req.speed = max(0.05, min(0.8, float(action.get("speed", 0.3))))
        self._toggle_awareness(False)
        self._call_service(self.cli_point_at, req, "POINT_AT")
        self._toggle_awareness(True)

    def _toggle_awareness(self, enabled: bool) -> None:
        if self.mock_mode or self.cli_toggle_awareness is None:
            return
        req = SetBool.Request()
        req.data = enabled
        self._call_service(self.cli_toggle_awareness, req, f"TOGGLE_AWARENESS {enabled}")

    def _call_service(self, client: Any, request: Any, description: str) -> None:
        if not client.wait_for_service(timeout_sec=self.service_timeout_sec):
            self.get_logger().warning(f"Service unavailable for {description}")
            return
        self.get_logger().info(f"Executing {description}")
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self, future, timeout_sec=self.service_timeout_sec + 5.0)
        result = future.result()
        if result is None:
            self.get_logger().warning(f"No response for {description}")
            return
        success = getattr(result, "success", None)
        message = getattr(result, "message", "")
        if success is False:
            self.get_logger().warning(f"{description} returned success=False message={message}")

    def _parse_color(self, value: Any) -> Tuple[int, int, int]:
        if isinstance(value, str):
            lower = value.strip().lower()
            if lower in COLOR_NAMES:
                return COLOR_NAMES[lower]
            if lower.startswith("#") and len(lower) == 7:
                try:
                    return (int(lower[1:3], 16), int(lower[3:5], 16), int(lower[5:7], 16))
                except Exception:
                    return COLOR_NAMES["white"]
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return self._scale_rgb(value[0], value[1], value[2])
        if isinstance(value, dict):
            return self._scale_rgb(value.get("red", value.get("r", 255)), value.get("green", value.get("g", 255)), value.get("blue", value.get("b", 255)))
        return COLOR_NAMES["white"]

    def _scale_rgb(self, red: Any, green: Any, blue: Any) -> Tuple[int, int, int]:
        vals = [float(red), float(green), float(blue)]
        if all(0.0 <= item <= 1.0 for item in vals):
            vals = [item * 255.0 for item in vals]
        return tuple(max(0, min(255, int(item))) for item in vals)  # type: ignore[return-value]

    def _normalize_language(self, language: str) -> str:
        lower = language.lower()
        if lower in ("es", "es-es", "es_co", "es-co", "spanish", "espanol"):
            return "Spanish"
        return language or self.default_language

    def _short(self, text: str, limit: int = 70) -> str:
        return text if len(text) <= limit else text[: limit - 3] + "..."

    def _speech_duration(self, text: str) -> float:
        return min(8.0, max(1.2, len(text) / 18.0))


def main() -> None:
    rclpy.init()
    node = BehaviorRendererNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
