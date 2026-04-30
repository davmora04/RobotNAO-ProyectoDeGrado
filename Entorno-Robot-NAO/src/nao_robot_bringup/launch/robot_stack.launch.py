from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    bridge_host = LaunchConfiguration("bridge_host")
    bridge_port = LaunchConfiguration("bridge_port")
    mock_mode = LaunchConfiguration("mock_mode")
    interactive_dialogue = LaunchConfiguration("interactive_dialogue")
    enable_speech_input = LaunchConfiguration("enable_speech_input")

    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "bridge_host",
                default_value=EnvironmentVariable("ROBOT_FEEDBACK_HOST", default_value="0.0.0.0"),
            ),
            DeclareLaunchArgument(
                "bridge_port",
                default_value=EnvironmentVariable("ROBOT_FEEDBACK_PORT", default_value="8765"),
            ),
            DeclareLaunchArgument(
                "mock_mode",
                default_value=EnvironmentVariable("NAO_MOCK_MODE", default_value="false"),
            ),
            DeclareLaunchArgument(
                "interactive_dialogue",
                default_value=EnvironmentVariable("NAO_INTERACTIVE_DIALOGUE", default_value="true"),
            ),
            DeclareLaunchArgument(
                "enable_speech_input",
                default_value=EnvironmentVariable("NAO_ENABLE_SPEECH_INPUT", default_value="true"),
            ),
            Node(
                package="nao_ds_bridge",
                executable="http_feedback_bridge",
                name="nao_ds_http_bridge",
                output="screen",
                parameters=[{"host": bridge_host, "port": bridge_port}],
            ),
            Node(
                package="nao_pedagogical_planner",
                executable="pedagogical_planner_node",
                name="nao_pedagogical_planner",
                output="screen",
                parameters=[{"compact_speech": False, "interactive_dialogue": interactive_dialogue}],
            ),
            Node(
                package="nao_dialogue_manager",
                executable="dialogue_manager_node",
                name="nao_dialogue_manager",
                output="screen",
            ),
            Node(
                package="nao_speech_to_text",
                executable="speech_to_text_node",
                name="nao_speech_to_text",
                output="screen",
                condition=IfCondition(enable_speech_input),
            ),
            Node(
                package="nao_behavior_renderer",
                executable="behavior_renderer_node",
                name="nao_behavior_renderer",
                output="screen",
                parameters=[{"mock_mode": mock_mode, "listen_legacy_plan": False}],
            ),
        ]
    )
