#!/usr/bin/env bash
set -eo pipefail

source install/setup.bash
ros2 launch nao_robot_bringup robot_stack.launch.py
