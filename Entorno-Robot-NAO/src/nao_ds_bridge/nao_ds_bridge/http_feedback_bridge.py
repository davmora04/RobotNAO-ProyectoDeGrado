#!/usr/bin/env python3
"""HTTP to ROS2 bridge for DS-Visualizer pedagogical feedback events."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


SUPPORTED_STATES = {
    "OPTIMAL",
    "CORRECT_WITH_ISSUES",
    "PARTIAL_SUCCESS",
    "CONCEPT_ERROR",
    "CRITICAL_FAILURE",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _infer_structure_type(payload: Dict[str, Any]) -> str:
    direct = (
        payload.get("structure_type")
        or payload.get("tipo_estructura")
        or payload.get("estructura")
        or ""
    )
    direct = str(direct).strip().lower()
    if direct:
        return direct

    operation = str(payload.get("operation") or payload.get("operacion_ejecutada") or "").lower()
    validation = _as_dict(payload.get("validation_result"))
    errors = validation.get("errores_detectados", [])
    joined_errors = " ".join(
        str(item.get("invariante", "")) + " " + str(item.get("detalle", ""))
        for item in errors
        if isinstance(item, dict)
    ).lower()
    text = f"{operation} {joined_errors}"

    if any(key in text for key in ("grafo", "vertice", "vertex", "arco", "edge", "adjacent", "dijkstra", "bfs", "dfs")):
        return "grafo_dirigido"
    if any(key in text for key in ("rbt", "red", "black", "color", "black_height", "root_is_black")):
        return "arbol_rbt"
    if any(key in text for key in ("bst", "inorder", "left", "right", "root", "ordering")):
        return "arbol_bst"
    if any(key in text for key in ("dll", "prev", "bidirectional")):
        return "lista_doble"
    if any(key in text for key in ("sll", "first", "last", "next", "size", "lista")):
        return "lista_enlazada"
    return "desconocida"


def normalize_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize several DS-Visualizer payload variants into a robot event."""
    validation = _as_dict(payload.get("validation_result"))
    feedback = _as_dict(payload.get("feedback") or payload.get("socratic_feedback"))

    # Accept the raw response from /solver/socratic-feedback.
    if not feedback and "preguntas" in payload:
        feedback = payload

    state = (
        payload.get("validation_state")
        or payload.get("estado")
        or validation.get("estado")
        or validation.get("state")
        or "UNKNOWN"
    )
    state = str(state).strip().upper()
    if state not in SUPPORTED_STATES:
        state = "CONCEPT_ERROR"

    score = payload.get("score", validation.get("score", 0))
    try:
        score = int(score)
    except Exception:
        score = 0

    operation = (
        payload.get("operation")
        or payload.get("operacion_ejecutada")
        or payload.get("operacion")
        or "operacion_desconocida"
    )

    return {
        "event_id": str(payload.get("event_id") or uuid.uuid4()),
        "session_id": str(payload.get("session_id") or payload.get("sessionId") or ""),
        "structure_type": _infer_structure_type(payload),
        "operation": str(operation),
        "validation_state": state,
        "score": score,
        "feedback": feedback,
        "validation_result": validation,
        "context": str(payload.get("context") or payload.get("contexto_estudiante") or ""),
        "metadata": _as_dict(payload.get("metadata")),
        "received_at": _utc_now(),
    }


class FeedbackRequestHandler(BaseHTTPRequestHandler):
    """Small JSON HTTP endpoint bound to a ROS node instance."""

    ros_node: "HttpFeedbackBridgeNode | None" = None

    server_version = "NAODSBridge/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if self.ros_node:
            self.ros_node.get_logger().info("[http] " + fmt % args)

    def _send_json(self, status: int, body: Dict[str, Any]) -> None:
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()
        self.wfile.write(encoded)

    def do_OPTIONS(self) -> None:
        self._send_json(200, {"ok": True})

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("", "/health", "/robot/health"):
            self._send_json(200, {"ok": True, "service": "nao_ds_bridge"})
            return
        self._send_json(404, {"ok": False, "error": "unknown endpoint"})

    def do_POST(self) -> None:
        if self.path != "/robot/feedback":
            self._send_json(404, {"ok": False, "error": "unknown endpoint"})
            return
        if self.ros_node is None:
            self._send_json(503, {"ok": False, "error": "ros node unavailable"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                raise ValueError("empty request body")
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
            event = normalize_event(payload)
            self.ros_node.publish_event(event)
            self._send_json(202, {"ok": True, "event_id": event["event_id"], "state": event["validation_state"]})
        except Exception as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})


class HttpFeedbackBridgeNode(Node):
    """Runs an HTTP server and publishes normalized feedback events to ROS2."""

    def __init__(self) -> None:
        super().__init__("nao_ds_http_bridge")

        default_host = os.getenv("ROBOT_FEEDBACK_HOST", "0.0.0.0")
        default_port = int(os.getenv("ROBOT_FEEDBACK_PORT", "8765"))
        self.declare_parameter("host", default_host)
        self.declare_parameter("port", default_port)
        self.declare_parameter("event_topic", "/ds_visualizer/feedback_event")
        self.declare_parameter("state_topic", "/interaction/state")
        self.declare_parameter("duplicate_window_sec", 10.0)

        self.host = self.get_parameter("host").get_parameter_value().string_value
        self.port = int(self.get_parameter("port").value)
        self.event_topic = self.get_parameter("event_topic").get_parameter_value().string_value
        self.state_topic = self.get_parameter("state_topic").get_parameter_value().string_value
        self.duplicate_window_sec = float(self.get_parameter("duplicate_window_sec").value)
        self._last_event_signature = ""
        self._last_event_time = 0.0

        self.pub_event = self.create_publisher(String, self.event_topic, 10)
        self.pub_state = self.create_publisher(String, self.state_topic, 10)

        FeedbackRequestHandler.ros_node = self
        self.httpd = ThreadingHTTPServer((self.host, self.port), FeedbackRequestHandler)
        self.server_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.server_thread.start()

        self.get_logger().info(
            f"HTTP bridge listening on http://{self.host}:{self.port}/robot/feedback -> {self.event_topic}"
        )

    def publish_event(self, event: Dict[str, Any]) -> None:
        signature = self._event_signature(event)
        now = time.monotonic()
        if (
            signature == self._last_event_signature
            and now - self._last_event_time < self.duplicate_window_sec
        ):
            self.get_logger().warning(
                f"Ignored duplicate event state={event['validation_state']} "
                f"operation={event['operation']} window={self.duplicate_window_sec:.1f}s"
            )
            return

        self._last_event_signature = signature
        self._last_event_time = now

        msg = String()
        msg.data = json.dumps(event, ensure_ascii=False)
        self.pub_event.publish(msg)
        self.pub_state.publish(String(data="ROBOT_EVENT_RECEIVED"))
        self.get_logger().info(
            f"Published event {event['event_id']} state={event['validation_state']} structure={event['structure_type']}"
        )

    def _event_signature(self, event: Dict[str, Any]) -> str:
        feedback = _as_dict(event.get("feedback"))
        questions = feedback.get("preguntas") or feedback.get("secuencia_preguntas") or []
        first_question = ""
        if isinstance(questions, list) and questions:
            question = questions[0]
            if isinstance(question, dict):
                first_question = str(question.get("pregunta") or question.get("texto") or question.get("question") or "")
            else:
                first_question = str(question)

        return json.dumps(
            {
                "session_id": event.get("session_id", ""),
                "structure_type": event.get("structure_type", ""),
                "operation": event.get("operation", ""),
                "validation_state": event.get("validation_state", ""),
                "question": first_question,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def destroy_node(self) -> bool:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        finally:
            return super().destroy_node()


def main() -> None:
    rclpy.init()
    node = HttpFeedbackBridgeNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
