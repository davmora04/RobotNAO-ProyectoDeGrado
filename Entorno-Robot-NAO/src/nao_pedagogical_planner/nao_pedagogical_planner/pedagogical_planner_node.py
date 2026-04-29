#!/usr/bin/env python3
"""Deterministic pedagogical planner for NAO interventions."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List

import rclpy
from rclpy.node import Node
from std_msgs.msg import String


STATE_CONFIG = {
    "CRITICAL_FAILURE": {
        "color": "red",
        "animation": "Stand/Gestures/Explain_1",
        "intro": "Alto un momento. La estructura quedo inconsistente. Revisemos el invariante principal antes de seguir.",
    },
    "CONCEPT_ERROR": {
        "color": "orange",
        "animation": "Stand/Gestures/Explain_1",
        "intro": "Aqui hay una pista conceptual. Pensemos la propiedad antes de tocar el codigo.",
    },
    "PARTIAL_SUCCESS": {
        "color": "yellow",
        "animation": "Stand/Gestures/Explain_1",
        "intro": "Vas cerca. La operacion funciona en parte, pero parece fallar en un caso borde.",
    },
    "CORRECT_WITH_ISSUES": {
        "color": "green",
        "animation": "Stand/Gestures/Hey_1",
        "intro": "La idea general funciona. Miremos un detalle para dejarla mas solida.",
    },
    "OPTIMAL": {
        "color": "green",
        "animation": "Stand/Gestures/Applause_1",
        "intro": "Buen trabajo. La estructura conserva sus invariantes.",
    },
}

STRUCTURE_LABELS = {
    "lista_enlazada": "lista enlazada",
    "lista_doble": "lista doblemente enlazada",
    "arbol_bst": "arbol binario de busqueda",
    "arbol_rbt": "arbol rojo negro",
    "grafo_dirigido": "grafo dirigido",
    "desconocida": "estructura",
}


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _clean_sentence(text: str, max_chars: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def _question_text(question: Any) -> str:
    if isinstance(question, dict):
        return str(question.get("pregunta") or question.get("texto") or question.get("question") or "")
    return str(question or "")


class PedagogicalPlannerNode(Node):
    """Converts validation feedback events into canonical NAO action plans."""

    def __init__(self) -> None:
        super().__init__("nao_pedagogical_planner")

        self.declare_parameter("event_topic", "/ds_visualizer/feedback_event")
        self.declare_parameter("plan_topic", "/nao_pedagogical/plan")
        self.declare_parameter("legacy_plan_topic", "/llm/plan")
        self.declare_parameter("publish_legacy_plan", True)
        self.declare_parameter("max_question_chars", 180)
        self.declare_parameter("max_intro_chars", 170)
        self.declare_parameter("student_pause_sec", 1.2)
        self.declare_parameter("compact_speech", False)

        self.event_topic = self.get_parameter("event_topic").get_parameter_value().string_value
        self.plan_topic = self.get_parameter("plan_topic").get_parameter_value().string_value
        self.legacy_plan_topic = self.get_parameter("legacy_plan_topic").get_parameter_value().string_value
        self.publish_legacy_plan = bool(self.get_parameter("publish_legacy_plan").value)
        self.max_question_chars = int(self.get_parameter("max_question_chars").value)
        self.max_intro_chars = int(self.get_parameter("max_intro_chars").value)
        self.student_pause_sec = float(self.get_parameter("student_pause_sec").value)
        self.compact_speech = bool(self.get_parameter("compact_speech").value)

        self.create_subscription(String, self.event_topic, self._on_event, 10)
        self.pub_plan = self.create_publisher(String, self.plan_topic, 10)
        self.pub_legacy_plan = self.create_publisher(String, self.legacy_plan_topic, 10)
        self.pub_state = self.create_publisher(String, "/interaction/state", 10)

        self.get_logger().info(f"Planner ready: {self.event_topic} -> {self.plan_topic}")

    def _on_event(self, msg: String) -> None:
        try:
            event = json.loads(msg.data)
            if not isinstance(event, dict):
                raise ValueError("event is not a JSON object")
            plan = self._build_plan(event)
            payload = json.dumps(plan, ensure_ascii=False)
            self.pub_plan.publish(String(data=payload))
            if self.publish_legacy_plan:
                self.pub_legacy_plan.publish(String(data=payload))
            self.pub_state.publish(String(data="ROBOT_PLAN_READY"))
            self.get_logger().info(
                f"Plan ready for event={event.get('event_id')} actions={len(plan.get('actions', []))}"
            )
        except Exception as exc:
            self.get_logger().error(f"Could not build plan: {exc}")

    def _build_plan(self, event: Dict[str, Any]) -> Dict[str, Any]:
        state = str(event.get("validation_state") or "CONCEPT_ERROR").upper()
        cfg = STATE_CONFIG.get(state, STATE_CONFIG["CONCEPT_ERROR"])
        structure_type = str(event.get("structure_type") or "desconocida")
        structure_label = STRUCTURE_LABELS.get(structure_type, structure_type.replace("_", " "))
        operation = str(event.get("operation") or "la operacion")
        feedback = _as_dict(event.get("feedback"))
        questions = _as_list(feedback.get("preguntas") or feedback.get("secuencia_preguntas"))
        question = _question_text(questions[0]) if questions else self._fallback_question(event)
        question_texts = [_clean_sentence(_question_text(item), self.max_question_chars) for item in questions]
        question_texts = [text for text in question_texts if text]
        if not question_texts:
            question_texts = [_clean_sentence(question, self.max_question_chars)]
        concept = str(feedback.get("concepto_clave") or self._concept_from_validation(event))

        intro = _clean_sentence(cfg["intro"], self.max_intro_chars)
        question = _clean_sentence(question, self.max_question_chars)
        concept = _clean_sentence(concept, 120)

        actions: List[Dict[str, Any]] = [
            {"type": "set_leds", "name": "FaceLeds", "color": cfg["color"], "duration": 0.5},
            {"type": "go_to_posture", "name": "Stand"},
        ]

        if self.compact_speech:
            if state == "OPTIMAL":
                compact_text = intro
            else:
                compact_text = question
            actions.append({"type": "say", "text": compact_text, "language": "es-ES", "animated": False})
        elif state == "OPTIMAL":
            actions.extend(
                [
                    {"type": "say", "text": intro, "language": "es-ES", "animated": True},
                    {"type": "play_animation", "animation_name": cfg["animation"]},
                ]
            )
        else:
            actions.extend(
                [
                    {"type": "say", "text": intro, "language": "es-ES", "animated": True},
                    {"type": "play_animation", "animation_name": cfg["animation"]},
                ]
            )
            for index, text in enumerate(question_texts, start=1):
                actions.append(
                    {
                        "type": "say",
                        "text": f"Pregunta {index}: {text}",
                        "language": "es-ES",
                        "animated": True,
                    }
                )
                if index < len(question_texts):
                    actions.append({"type": "pause", "seconds": 0.6})
            actions.extend(
                [
                    {"type": "pause", "seconds": self.student_pause_sec},
                    {
                        "type": "say",
                        "text": f"El concepto clave es {concept}. Intenta responder antes de cambiar el codigo.",
                        "language": "es-ES",
                        "animated": True,
                    },
                ]
            )

        return {
            "meta": {
                "plan_id": int(time.time() * 1000),
                "source": "ds_visualizer",
                "event_id": event.get("event_id"),
                "validation_state": state,
                "structure_type": structure_type,
                "operation": operation,
            },
            "actions": actions,
        }

    def _fallback_question(self, event: Dict[str, Any]) -> str:
        structure = str(event.get("structure_type") or "")
        operation = str(event.get("operation") or "la operacion")
        if "grafo" in structure:
            return f"Que vertices, arcos y adyacencias deberian existir despues de {operation}?"
        if "arbol" in structure:
            return f"Que recorrido inorder esperarias despues de {operation}?"
        return f"Como deberian quedar first, last y size despues de {operation}?"

    def _concept_from_validation(self, event: Dict[str, Any]) -> str:
        validation = _as_dict(event.get("validation_result"))
        errors = _as_list(validation.get("errores_detectados"))
        if errors and isinstance(errors[0], dict):
            invariant = str(errors[0].get("invariante") or errors[0].get("test_fallido") or "")
            if invariant:
                return invariant.replace("_", " ")
        return "invariantes de la estructura"


def main() -> None:
    rclpy.init()
    node = PedagogicalPlannerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
