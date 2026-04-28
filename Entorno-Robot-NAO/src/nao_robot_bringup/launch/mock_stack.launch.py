from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import EnvironmentVariable, LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    bridge_host = LaunchConfiguration("bridge_host")
    bridge_port = LaunchConfiguration("bridge_port")

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
            ),
            Node(
                package="nao_behavior_renderer",
                executable="behavior_renderer_node",
                name="nao_behavior_renderer",
                output="screen",
                parameters=[{"mock_mode": True, "listen_legacy_plan": False}],
            ),
        ]
    )
