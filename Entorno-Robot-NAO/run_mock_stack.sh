#!/usr/bin/env bash
set -eo pipefail

source install/setup.bash
ros2 launch nao_robot_bringup mock_stack.launch.py
