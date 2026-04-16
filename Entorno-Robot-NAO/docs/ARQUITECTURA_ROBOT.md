# Arquitectura del entorno robot

## Flujo

```text
Frontend React
   |
   v
Backend Flask
   |
   | validacion BET+CLP
   | agente socratico
   v
HTTP POST al robot
   |
   v
nao_ds_bridge
   |
   v
nao_pedagogical_planner
   |
   v
nao_behavior_renderer
   |
   v
NAOqi bridge
```

## Topicos

| Topico | Publica | Consume | Payload |
| --- | --- | --- | --- |
| `/ds_visualizer/feedback_event` | `nao_ds_bridge` | `nao_pedagogical_planner` | Evento pedagogico JSON |
| `/nao_pedagogical/plan` | `nao_pedagogical_planner` | `nao_behavior_renderer` | Plan JSON de acciones |
| `/llm/plan` | `nao_pedagogical_planner` | compatible con renderers previos | Plan JSON de acciones |
| `/interaction/state` | todos | observadores | Estado textual |

## Acciones canonicas

El renderer entiende estas acciones:

```json
{ "type": "say", "text": "Texto", "language": "es-ES", "animated": true }
{ "type": "set_leds", "name": "FaceLeds", "color": "orange", "duration": 0.5 }
{ "type": "go_to_posture", "name": "Stand" }
{ "type": "play_animation", "animation_name": "Stand/Gestures/Explain_1" }
{ "type": "move_to", "x": 0.2, "y": 0.0, "theta": 0.0 }
{ "type": "point_at", "effector": "RArm", "x": 0.3, "y": 0.0, "z": 0.9 }
{ "type": "pause", "seconds": 1.2 }
```

## Principio de diseno

El planner no debe resolver el error del estudiante. Solo convierte el feedback socratico en una intervencion
fisica breve:

- llamar la atencion,
- reforzar el estado del error,
- leer una pregunta socratica,
- cerrar con el concepto clave.

## Comparacion con ArquitecturaEmocionalNAO

Se hereda:

- uso de ROS2 por paquetes,
- planes JSON como frontera entre planeacion y ejecucion,
- renderer desacoplado de NAOqi,
- modo robusto ante servicios no disponibles.

Se evita:

- mezclar percepcion emocional con el caso pedagogico,
- depender de audio/camara para el MVP,
- usar `head_touch` como orquestador principal,
- acoplar el robot a la logica interna de DS-Visualizer.

