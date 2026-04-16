# Contrato de evento pedagogico para NAO

El entorno del robot recibe eventos HTTP en:

```text
POST /robot/feedback
Content-Type: application/json
```

Puerto por defecto:

```text
http://ROBOT_IP:8765/robot/feedback
```

## Formato canonico

```json
{
  "session_id": "session-id",
  "structure_type": "lista_enlazada",
  "operation": "add_first",
  "validation_state": "CONCEPT_ERROR",
  "score": 50,
  "feedback": {
    "preguntas": [
      {
        "orden": 1,
        "categoria": "reconocimiento",
        "pregunta": "Pregunta socratica para el estudiante"
      }
    ],
    "pistas": [],
    "resumen_error": "Resumen breve",
    "concepto_clave": "Concepto a reforzar"
  },
  "context": "Descripcion opcional de lo que hizo el estudiante"
}
```

## Formato compatible con DS-Visualizer

El bridge tambien acepta el formato que ya usa el backend:

```json
{
  "tipo_estructura": "lista_enlazada",
  "operacion_ejecutada": "add_first",
  "validation_result": {
    "estado": "CONCEPT_ERROR",
    "score": 50,
    "errores_detectados": []
  },
  "feedback": {
    "preguntas": []
  }
}
```

## Estados soportados

- `OPTIMAL`
- `CORRECT_WITH_ISSUES`
- `PARTIAL_SUCCESS`
- `CONCEPT_ERROR`
- `CRITICAL_FAILURE`

## Flujo ROS2 interno

```text
HTTP /robot/feedback
        |
        v
/ds_visualizer/feedback_event
        |
        v
/nao_pedagogical/plan
        |
        v
NAOqi services: say, leds, posture, animation
```

