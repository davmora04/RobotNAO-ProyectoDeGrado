#!/usr/bin/env python3
"""Turn-based pedagogical dialogue manager for DS-Visualizer feedback."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

try:
    from google import genai
    from google.genai import types

    GOOGLE_GENAI_AVAILABLE = True
except Exception:  # pragma: no cover - runtime dependency may be installed outside tests
    genai = None
    types = None
    GOOGLE_GENAI_AVAILABLE = False


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _clean(text: Any, limit: int = 260) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].strip()


def _env_bool(name: str, default: bool) -> bool:
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


class DialogueManagerNode(Node):
    """Runs one Socratic question at a time and answers student voice turns."""

    def __init__(self) -> None:
        super().__init__("nao_dialogue_manager")

        self.declare_parameter("session_topic", "/nao_dialogue/session")
        self.declare_parameter("student_text_topic", "/nao_dialogue/student_text")
        self.declare_parameter("plan_topic", "/nao_pedagogical/plan")
        self.declare_parameter("use_vertex_ai", _env_bool("USE_VERTEX_AI", True))
        self.declare_parameter("vertex_project", os.getenv("VERTEX_AI_PROJECT", "microservices-459904"))
        self.declare_parameter("vertex_location", os.getenv("VERTEX_AI_LOCATION", "us-central1"))
        self.declare_parameter("google_ai_model", os.getenv("GOOGLE_AI_MODEL", "gemini-2.5-flash"))
        self.declare_parameter("google_ai_api_key", os.getenv("GOOGLE_AI_API_KEY", os.getenv("GEMINI_API_KEY", "")))
        self.declare_parameter("timeout_sec", 35.0)
        self.declare_parameter("max_turns_per_question", 2)
        self.declare_parameter("use_llm", _env_bool("NAO_DIALOGUE_USE_LLM", True))

        self.session_topic = self.get_parameter("session_topic").get_parameter_value().string_value
        self.student_text_topic = self.get_parameter("student_text_topic").get_parameter_value().string_value
        self.plan_topic = self.get_parameter("plan_topic").get_parameter_value().string_value
        self.use_vertex_ai = _coerce_bool(self.get_parameter("use_vertex_ai").value)
        self.vertex_project = self.get_parameter("vertex_project").get_parameter_value().string_value
        self.vertex_location = self.get_parameter("vertex_location").get_parameter_value().string_value
        self.google_ai_model = self.get_parameter("google_ai_model").get_parameter_value().string_value
        self.google_ai_api_key = self.get_parameter("google_ai_api_key").get_parameter_value().string_value
        self.timeout_sec = float(self.get_parameter("timeout_sec").value)
        self.max_turns_per_question = int(self.get_parameter("max_turns_per_question").value)
        self.use_llm = _coerce_bool(self.get_parameter("use_llm").value)
        self.genai_client = self._build_genai_client()

        self.create_subscription(String, self.session_topic, self._on_session, 10)
        self.create_subscription(String, self.student_text_topic, self._on_student_text, 10)
        self.pub_plan = self.create_publisher(String, self.plan_topic, 10)
        self.pub_state = self.create_publisher(String, "/interaction/state", 10)

        self.session: Dict[str, Any] | None = None
        self.question_index = 0
        self.turns_for_current_question = 0
        self.history: List[Dict[str, str]] = []
        self.active_event_id = ""
        self.last_session_time = 0.0

        self.get_logger().info(
            f"Dialogue manager ready: {self.session_topic} + {self.student_text_topic} -> {self.plan_topic}"
        )

    def _build_genai_client(self) -> Any:
        if not self.use_llm:
            self.get_logger().info("Dialogue LLM disabled; using local fallback.")
            return None
        if not GOOGLE_GENAI_AVAILABLE:
            self.get_logger().warning("google-genai is not installed; using local fallback.")
            return None
        try:
            if self.use_vertex_ai:
                if not self.vertex_project:
                    self.get_logger().warning("VERTEX_AI_PROJECT is empty; using local fallback.")
                    return None
                self.get_logger().info(
                    f"Dialogue manager using Vertex AI project={self.vertex_project} location={self.vertex_location} model={self.google_ai_model}"
                )
                return genai.Client(
                    vertexai=True,
                    project=self.vertex_project,
                    location=self.vertex_location,
                    http_options=types.HttpOptions(api_version="v1"),
                )
            if not self.google_ai_api_key:
                self.get_logger().warning("GOOGLE_AI_API_KEY/GEMINI_API_KEY is empty; using local fallback.")
                return None
            self.get_logger().info(f"Dialogue manager using Google AI model={self.google_ai_model}")
            return genai.Client(api_key=self.google_ai_api_key)
        except Exception as exc:
            self.get_logger().warning(f"Could not initialize google-genai client: {exc}")
            return None

    def _on_session(self, msg: String) -> None:
        try:
            session = json.loads(msg.data)
            if not isinstance(session, dict):
                raise ValueError("session must be a JSON object")
        except Exception as exc:
            self.get_logger().error(f"Invalid dialogue session: {exc}")
            return

        questions = [_clean(q, 260) for q in _as_list(session.get("questions")) if _clean(q)]
        if not questions:
            self.get_logger().warning("Dialogue session without questions; ignored.")
            return

        event_id = str(session.get("event_id") or session.get("session_id") or "")
        now = self.get_clock().now().nanoseconds / 1e9
        if self.session and event_id and event_id == self.active_event_id and now - self.last_session_time < 3.0:
            self.get_logger().warning(f"Ignored duplicate dialogue session event={event_id}")
            return

        session["questions"] = questions
        self.session = session
        self.active_event_id = event_id
        self.last_session_time = now
        self.question_index = 0
        self.turns_for_current_question = 0
        self.history.clear()
        self.pub_state.publish(String(data="DIALOGUE_STARTED"))
        self.get_logger().info(
            f"Started dialogue event={session.get('event_id')} questions={len(questions)}"
        )
        self._ask_current_question(include_intro=True)

    def _on_student_text(self, msg: String) -> None:
        text = _clean(msg.data, 420)
        if not self.session:
            self.get_logger().warning(f"Student text ignored without active dialogue: {text!r}")
            return
        if not text:
            self._publish_plan(
                [
                    {"type": "set_leds", "name": "FaceLeds", "color": "yellow", "duration": 0.5},
                    {
                        "type": "say",
                        "text": "No alcance a escucharte bien. Toca mi cabeza e intenta decirlo otra vez.",
                        "language": "es-ES",
                        "animated": True,
                    },
                ],
                "DIALOGUE_LISTEN_RETRY",
            )
            return

        lower = text.lower()
        if self._is_repeat_request(lower):
            self._ask_current_question(include_intro=False)
            return
        if self._is_continue_request(lower):
            self._advance_or_finish()
            return

        self.turns_for_current_question += 1
        reply = self._build_reply(text)
        robot_text = _clean(reply.get("respuesta_robot"), 320) or self._fallback_reply(text)

        self.history.append({"student": text, "robot": robot_text})
        should_advance = bool(reply.get("avanzar_siguiente_pregunta"))
        if self.turns_for_current_question >= self.max_turns_per_question and not self._is_confusion(lower):
            should_advance = True

        actions: List[Dict[str, Any]] = [
            {
                "type": "set_leds",
                "name": "FaceLeds",
                "color": str(reply.get("color") or "blue"),
                "duration": 0.5,
            },
            {"type": "go_to_posture", "name": "Stand"},
            {
                "type": "say",
                "text": robot_text,
                "language": "es-ES",
                "animated": True,
            },
        ]
        animation = str(reply.get("animacion") or "Stand/Gestures/Explain_1").strip()
        if animation:
            actions.append({"type": "play_animation", "animation_name": animation})

        if should_advance:
            followup_actions, finished = self._advance_followup_actions()
            actions.extend(followup_actions)
            self._publish_plan(actions, "DIALOGUE_REPLY_ADVANCING")
            if finished:
                self.pub_state.publish(String(data="DIALOGUE_DONE"))
                self.session = None
        else:
            actions.append(
                {
                    "type": "say",
                    "text": "Cuando quieras seguir, dime continuar. Si quieres, tambien puedes preguntarme otra cosa.",
                    "language": "es-ES",
                    "animated": True,
                }
            )
            self._publish_plan(actions, "DIALOGUE_REPLY_WAITING")

    def _ask_current_question(self, include_intro: bool) -> None:
        if not self.session:
            return
        questions = _as_list(self.session.get("questions"))
        question = questions[self.question_index]
        total = len(questions)
        self.turns_for_current_question = 0

        actions: List[Dict[str, Any]] = [
            {
                "type": "set_leds",
                "name": "FaceLeds",
                "color": str(self.session.get("color") or "orange"),
                "duration": 0.5,
            },
            {"type": "go_to_posture", "name": "Stand"},
        ]
        if include_intro:
            intro = _clean(self.session.get("intro"), 180)
            if intro:
                actions.append({"type": "say", "text": intro, "language": "es-ES", "animated": True})
            animation = str(self.session.get("animation") or "Stand/Gestures/Explain_1")
            actions.append({"type": "play_animation", "animation_name": animation})

        actions.extend(
            [
                {
                    "type": "say",
                    "text": f"Pregunta {self.question_index + 1} de {total}: {question}",
                    "language": "es-ES",
                    "animated": True,
                },
                {
                    "type": "say",
                    "text": "Toca mi cabeza, responde o hazme una pregunta. Cuando termines, toca de nuevo para enviar tu voz.",
                    "language": "es-ES",
                    "animated": True,
                },
            ]
        )
        self._publish_plan(actions, "DIALOGUE_QUESTION")

    def _advance_or_finish(self) -> None:
        if not self.session:
            return
        followup_actions, finished = self._advance_followup_actions()
        self._publish_plan(followup_actions, "DIALOGUE_CONTINUE")
        if finished:
            self.pub_state.publish(String(data="DIALOGUE_DONE"))
            self.session = None

    def _advance_followup_actions(self) -> tuple[List[Dict[str, Any]], bool]:
        if not self.session:
            return [], True
        questions = _as_list(self.session.get("questions"))
        self.question_index += 1
        if self.question_index >= len(questions):
            concept = _clean(self.session.get("concept"), 140)
            text = "Terminamos esta guia. Intenta ajustar tu implementacion y vuelve a validar."
            if concept:
                text = f"Terminamos esta guia. El concepto clave es {concept}. Intenta ajustar tu implementacion y vuelve a validar."
            return [
                {"type": "pause", "seconds": 0.5},
                {"type": "set_leds", "name": "FaceLeds", "color": "green", "duration": 0.5},
                {"type": "say", "text": text, "language": "es-ES", "animated": True},
                {"type": "play_animation", "animation_name": "Stand/Gestures/Hey_1"},
            ], True

        self.turns_for_current_question = 0
        question = questions[self.question_index]
        total = len(questions)
        return [
            {"type": "pause", "seconds": 0.5},
            {
                "type": "say",
                "text": f"Ahora vamos con la pregunta {self.question_index + 1} de {total}: {question}",
                "language": "es-ES",
                "animated": True,
            },
            {
                "type": "say",
                "text": "Toca mi cabeza para responder o preguntarme algo sobre esta parte.",
                "language": "es-ES",
                "animated": True,
            },
        ], False

    def _build_reply(self, student_text: str) -> Dict[str, Any]:
        if self.use_llm and self.genai_client:
            try:
                return self._call_gemini(student_text)
            except Exception as exc:
                self.get_logger().warning(f"LLM dialogue failed, using fallback: {exc}")
        return {
            "respuesta_robot": self._fallback_reply(student_text),
            "avanzar_siguiente_pregunta": False,
            "tipo_intervencion": "fallback",
            "color": "blue",
            "animacion": "Stand/Gestures/Explain_1",
        }

    def _call_gemini(self, student_text: str) -> Dict[str, Any]:
        assert self.session is not None
        questions = _as_list(self.session.get("questions"))
        payload_context = {
            "validation_state": self.session.get("validation_state"),
            "structure_type": self.session.get("structure_type"),
            "operation": self.session.get("operation"),
            "current_question_number": self.question_index + 1,
            "current_question": questions[self.question_index],
            "all_questions": questions,
            "concept": self.session.get("concept"),
            "feedback": self.session.get("feedback"),
            "validation_result": self.session.get("validation_result"),
            "student_text": student_text,
            "short_history": self.history[-4:],
        }
        system = (
            "Eres NAO como tutor socratico de estructuras de datos. "
            "Responde en espanol, breve y conversacional, maximo 35 palabras en respuesta_robot. "
            "No des codigo ni la solucion directa. "
            "Si el estudiante pregunta algo, aclara el concepto con una pregunta guia. "
            "Solo permite avanzar cuando el estudiante lo pide claramente o muestra comprension suficiente. "
            "Devuelve solo JSON compacto de una linea con estas claves exactas: "
            "respuesta_robot, avanzar_siguiente_pregunta, tipo_intervencion, color, animacion. "
            "No uses saltos de linea dentro de strings. No uses comillas dobles dentro de respuesta_robot. "
            "Animaciones validas: Stand/Gestures/Explain_1, Stand/Gestures/Hey_1, Stand/Gestures/Applause_1."
        )
        prompt = (
            json.dumps(payload_context, ensure_ascii=False)
            + "\n\nResponde unicamente con un objeto JSON valido, sin markdown ni texto adicional."
        )
        response = self.genai_client.models.generate_content(
            model=self.google_ai_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.25,
                max_output_tokens=900,
                response_mime_type="application/json",
            ),
        )
        text = response.text or ""
        if not text:
            raise RuntimeError("empty Gemini response")
        return self._parse_llm_json(text)

    def _parse_llm_json(self, text: str) -> Dict[str, Any]:
        raw_text = text
        text = self._strip_json_envelope(text)
        try:
            data = json.loads(text)
            return self._normalize_llm_reply(data if isinstance(data, dict) else {})
        except json.JSONDecodeError as exc:
            self.get_logger().warning(f"Gemini returned malformed JSON; trying tolerant parse: {exc}")
            self.get_logger().warning(f"Gemini raw response preview: {raw_text[:500]!r}")
            repaired = self._tolerant_reply_parse(text)
            if repaired:
                return self._normalize_llm_reply(repaired)
            raise

    def _strip_json_envelope(self, text: str) -> str:
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:].strip()
        elif text.startswith("```"):
            text = text[3:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return text[start : end + 1]
        return text

    def _normalize_llm_reply(self, data: Dict[str, Any]) -> Dict[str, Any]:
        animation = str(data.get("animacion") or "Stand/Gestures/Explain_1").strip()
        allowed_animations = {
            "Stand/Gestures/Explain_1",
            "Stand/Gestures/Hey_1",
            "Stand/Gestures/Applause_1",
        }
        if animation not in allowed_animations:
            animation = "Stand/Gestures/Explain_1"

        color = str(data.get("color") or "blue").strip().lower()
        allowed_colors = {"blue", "green", "yellow", "orange", "red", "purple", "white"}
        if color not in allowed_colors:
            color = "blue"

        return {
            "respuesta_robot": _clean(data.get("respuesta_robot"), 320),
            "avanzar_siguiente_pregunta": bool(data.get("avanzar_siguiente_pregunta", False)),
            "tipo_intervencion": str(data.get("tipo_intervencion") or "aclaracion"),
            "color": color,
            "animacion": animation,
        }

    def _tolerant_reply_parse(self, text: str) -> Dict[str, Any]:
        text = " ".join(text.split())
        reply = self._extract_json_string_value(text, "respuesta_robot")
        if not reply:
            return {}
        advance_match = re.search(r'"avanzar_siguiente_pregunta"\s*:\s*(true|false)', text, re.IGNORECASE)
        color = self._extract_json_string_value(text, "color") or "blue"
        intervention = self._extract_json_string_value(text, "tipo_intervencion") or "aclaracion"
        animation = self._extract_json_string_value(text, "animacion") or "Stand/Gestures/Explain_1"
        return {
            "respuesta_robot": reply,
            "avanzar_siguiente_pregunta": bool(advance_match and advance_match.group(1).lower() == "true"),
            "tipo_intervencion": intervention,
            "color": color,
            "animacion": animation,
        }

    def _extract_json_string_value(self, text: str, key: str) -> str:
        pattern = rf'"{re.escape(key)}"\s*:\s*"(?P<value>.*?)(?:"\s*,\s*"[^"]+"\s*:|"\s*}})'
        match = re.search(pattern, text, re.DOTALL)
        if not match:
            return ""
        value = match.group("value")
        value = re.sub(r'"\s*,\s*$', "", value)
        return _clean(value.replace('\\"', '"'), 320)

    def _fallback_reply(self, student_text: str) -> str:
        assert self.session is not None
        questions = _as_list(self.session.get("questions"))
        question = questions[self.question_index]
        concept = _clean(self.session.get("concept"), 120)
        text = student_text.lower()
        if self._is_confusion(text):
            return f"Vamos por partes. Para esta pregunta, piensa primero en la propiedad que debe conservarse: {concept}. Que dato de la estructura podrias revisar para comprobarlo?"
        if "por que" in text or "por qué" in text:
            return "Buena pregunta. El punto es conectar el resultado visible con el invariante. En tus palabras, que tendria que cumplirse para que esto fuera correcto?"
        return f"Te escucho. Relaciona tu idea con esta pregunta: {question} Que evidencia de la estructura te ayudaria a confirmarlo?"

    def _is_continue_request(self, text: str) -> bool:
        return any(key in text for key in ("continuar", "continua", "siguiente", "sigamos", "pasemos", "seguir"))

    def _is_repeat_request(self, text: str) -> bool:
        return any(key in text for key in ("repite", "repetir", "otra vez", "no escuche", "no escuché"))

    def _is_confusion(self, text: str) -> bool:
        return any(key in text for key in ("no entiendo", "no se", "no sé", "explicame", "explícame", "que significa", "qué significa"))

    def _publish_plan(self, actions: List[Dict[str, Any]], state: str) -> None:
        plan = {
            "meta": {
                "plan_id": int(time.time() * 1000),
                "source": "nao_dialogue_manager",
                "dialogue_state": state,
                "question_index": self.question_index,
                "event_id": self.session.get("event_id") if self.session else None,
            },
            "actions": actions,
        }
        self.pub_plan.publish(String(data=json.dumps(plan, ensure_ascii=False)))
        self.pub_state.publish(String(data=state))
        self.get_logger().info(f"Published dialogue plan state={state} actions={len(actions)}")


def main() -> None:
    rclpy.init()
    node = DialogueManagerNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
