# Integracion desde Flask

Este entorno no modifica el backend actual. Cuando quieran conectarlo, agreguen una llamada HTTP desde
`socratic_agent_service.py` o desde el endpoint `/solver/socratic-feedback`, despues de generar el feedback.

Ejemplo minimo:

```python
import os
import requests


def enviar_feedback_a_robot(payload):
    robot_url = os.getenv("NAO_ROBOT_FEEDBACK_URL", "http://127.0.0.1:8765/robot/feedback")
    try:
        response = requests.post(robot_url, json=payload, timeout=2.5)
        return {
            "robot_exito": response.status_code in (200, 202),
            "robot_status": response.status_code,
            "robot_respuesta": response.json() if response.content else {},
        }
    except Exception as exc:
        return {
            "robot_exito": False,
            "robot_error": str(exc),
        }
```

Payload recomendado:

```python
robot_payload = {
    "session_id": session_id,
    "tipo_estructura": tipo_estructura,
    "operacion_ejecutada": operacion,
    "validation_result": validation_result,
    "feedback": resultado_agente["feedback"],
    "contexto_estudiante": contexto_estudiante,
}
```

Para preservar el fallback texto-only, la llamada al robot debe ser no bloqueante o de timeout corto.

