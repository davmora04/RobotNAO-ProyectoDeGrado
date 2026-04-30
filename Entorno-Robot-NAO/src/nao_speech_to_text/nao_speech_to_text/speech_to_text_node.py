#!/usr/bin/env python3
"""Head-touch controlled speech-to-text publisher for NAO dialogue."""

from __future__ import annotations

import array
from typing import Any

import rclpy
from rclpy.node import Node
from std_msgs.msg import String

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None

try:
    import whisper
except Exception:  # pragma: no cover
    whisper = None

try:
    from naoqi_bridge_msgs.msg import AudioBuffer, HeadTouch
except Exception:  # pragma: no cover
    AudioBuffer = None
    HeadTouch = None


class SpeechToTextNode(Node):
    """Records NAO microphone audio between two head touches and transcribes it."""

    def __init__(self) -> None:
        super().__init__("nao_speech_to_text")

        self.declare_parameter("audio_topic", "/mic")
        self.declare_parameter("head_touch_topic", "/head_touch")
        self.declare_parameter("student_text_topic", "/nao_dialogue/student_text")
        self.declare_parameter("legacy_asr_topic", "/asr/text")
        self.declare_parameter("whisper_model", "base")
        self.declare_parameter("language", "es")
        self.declare_parameter("touch_debounce_sec", 0.9)
        self.declare_parameter("min_recording_sec", 1.0)

        self.audio_topic = self.get_parameter("audio_topic").get_parameter_value().string_value
        self.head_touch_topic = self.get_parameter("head_touch_topic").get_parameter_value().string_value
        self.student_text_topic = self.get_parameter("student_text_topic").get_parameter_value().string_value
        self.legacy_asr_topic = self.get_parameter("legacy_asr_topic").get_parameter_value().string_value
        self.whisper_model_name = self.get_parameter("whisper_model").get_parameter_value().string_value
        self.language = self.get_parameter("language").get_parameter_value().string_value
        self.touch_debounce_sec = float(self.get_parameter("touch_debounce_sec").value)
        self.min_recording_sec = float(self.get_parameter("min_recording_sec").value)

        self.recording = False
        self.audio_buffer = bytearray()
        self.model: Any = None
        self.last_touch_time = 0.0
        self.recording_started_at = 0.0

        self.pub_student = self.create_publisher(String, self.student_text_topic, 10)
        self.pub_legacy = self.create_publisher(String, self.legacy_asr_topic, 10)
        self.pub_state = self.create_publisher(String, "/interaction/state", 10)
        self.create_subscription(String, "/interaction/state", self._on_interaction_state, 10)

        self.armed_for_student = False
        self.waiting_for_plan_done = False
        self.touch_is_down = False

        if AudioBuffer is None or HeadTouch is None:
            self.get_logger().error("naoqi_bridge_msgs is not available; speech node cannot subscribe to NAO audio.")
            return
        if np is None or whisper is None:
            self.get_logger().error("numpy/openai-whisper is not available; install them before using speech input.")
            return

        self.model = whisper.load_model(self.whisper_model_name)
        self.create_subscription(AudioBuffer, self.audio_topic, self._on_audio, 10)
        self.create_subscription(HeadTouch, self.head_touch_topic, self._on_touch, 10)
        self.get_logger().info(
            f"Speech-to-text ready. Touch head to start/stop recording; output={self.student_text_topic}"
        )

    def _on_touch(self, msg: Any) -> None:
        state = getattr(msg, "state", 0)
        if state != 1:
            self.touch_is_down = False
            return
        if self.touch_is_down:
            return
        self.touch_is_down = True

        if not self.armed_for_student and not self.recording:
            self.get_logger().info("Ignoring head touch while robot is not waiting for student speech.")
            return

        now = self.get_clock().now().nanoseconds / 1e9
        if now - self.last_touch_time < self.touch_debounce_sec:
            return
        self.last_touch_time = now

        if not self.recording:
            self.recording = True
            self.recording_started_at = now
            self.armed_for_student = True
            self.audio_buffer.clear()
            self.pub_state.publish(String(data="DIALOGUE_RECORDING"))
            self.get_logger().info("Recording started.")
            return

        if now - self.recording_started_at < self.min_recording_sec:
            self.get_logger().info("Ignoring stop touch because recording is too short.")
            return

        self.recording = False
        self.armed_for_student = False
        self.pub_state.publish(String(data="DIALOGUE_TRANSCRIBING"))
        self.get_logger().info("Recording stopped. Transcribing...")
        text = self._transcribe(bytes(self.audio_buffer)) if self.audio_buffer else ""
        self.pub_student.publish(String(data=text))
        self.pub_legacy.publish(String(data=text))
        self.pub_state.publish(String(data="DIALOGUE_TRANSCRIBED"))
        self.get_logger().info(f"Transcript: {text!r}")

    def _on_interaction_state(self, msg: String) -> None:
        state = str(msg.data or "")
        if state in {
            "DIALOGUE_QUESTION",
            "DIALOGUE_REPLY_WAITING",
            "DIALOGUE_CONTINUE",
            "DIALOGUE_LISTEN_RETRY",
        }:
            self.waiting_for_plan_done = True
            self.armed_for_student = False
            return
        if state == "ROBOT_PLAN_RUNNING":
            self.armed_for_student = False
            return
        if state == "ROBOT_PLAN_DONE" and self.waiting_for_plan_done:
            self.waiting_for_plan_done = False
            self.armed_for_student = True
            self.get_logger().info("Student speech input armed. Touch head to start recording.")
            return
        if state in {"DIALOGUE_DONE", "ROBOT_DIALOGUE_READY", "DIALOGUE_TRANSCRIBING"}:
            self.waiting_for_plan_done = False
            self.armed_for_student = False

    def _on_audio(self, msg: Any) -> None:
        if not self.recording:
            return
        data = getattr(msg, "data", [])
        try:
            if np is not None:
                audio = np.array(data, dtype=np.int16)
                self.audio_buffer.extend(audio.tobytes())
            else:
                self.audio_buffer.extend(array.array("h", data).tobytes())
        except Exception as exc:
            self.get_logger().warning(f"Could not append audio buffer: {exc}")

    def _transcribe(self, raw_audio_bytes: bytes) -> str:
        if self.model is None or np is None:
            return ""
        try:
            int_audio = np.frombuffer(raw_audio_bytes, dtype=np.int16)
            float_audio = int_audio.astype(np.float32) / 32768.0
            result = self.model.transcribe(float_audio, language=self.language)
            return " ".join(str(result.get("text", "")).split())
        except Exception as exc:
            self.get_logger().error(f"Whisper transcription failed: {exc}")
            return ""


def main() -> None:
    rclpy.init()
    node = SpeechToTextNode()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
