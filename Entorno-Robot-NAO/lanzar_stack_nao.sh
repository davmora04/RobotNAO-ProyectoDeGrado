#!/usr/bin/env bash
set -e

cd /home/robotica/Documents/RobotNAO-DMR-NPR/RobotNAO-ProyectoDeGrado/Entorno-Robot-NAO

source /opt/ros/jazzy/setup.bash
source /home/robotica/ros2_naoqi_ws/install/setup.bash
source install/setup.bash

export ROBOT_FEEDBACK_HOST=0.0.0.0
export ROBOT_FEEDBACK_PORT=8765
export NAO_INTERACTIVE_DIALOGUE=true
export NAO_ENABLE_SPEECH_INPUT=true

export USE_VERTEX_AI=true
export VERTEX_AI_PROJECT=microservices-459904
export VERTEX_AI_LOCATION=us-central1
export GOOGLE_AI_MODEL=gemini-2.5-flash

ros2 launch nao_robot_bringup robot_stack.launch.py
