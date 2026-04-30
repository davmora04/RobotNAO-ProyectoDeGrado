# Entorno Robot NAO para DS-Visualizer

Este directorio contiene el entorno aislado del robot para el proyecto de David Mora y Nicolas Perez.
No modifica el backend ni el frontend existentes. Su responsabilidad es recibir eventos pedagogicos de
DS-Visualizer y convertirlos en intervenciones multimodales del robot NAO.

## Objetivo

Conectar la retroalimentacion socratica generada por DS-Visualizer con NAO, usando una arquitectura
modular inspirada en `ArquitecturaEmocionalNAO` de Juan Pablo Pena:

```text
DS-Visualizer Backend
        |
        | HTTP POST /robot/feedback
        v
nao_ds_bridge
        |
        | /ds_visualizer/feedback_event
        v
nao_pedagogical_planner
        |
        | /nao_pedagogical/plan
        v
nao_behavior_renderer
        |
        | NAOqi bridge services
        v
Robot NAO
```

## Paquetes ROS2

- `nao_ds_bridge`: abre un endpoint HTTP y publica eventos normalizados en ROS2.
- `nao_pedagogical_planner`: transforma errores BET+CLP y feedback socratico en planes de accion.
- `nao_dialogue_manager`: ejecuta el feedback por turnos, una pregunta a la vez, y responde a intervenciones del estudiante.
- `nao_speech_to_text`: graba audio entre dos toques de cabeza y publica la transcripcion para el dialogo.
- `nao_behavior_renderer`: ejecuta planes mediante NAOqi o los simula en modo mock.
- `nao_robot_bringup`: launch files para levantar todo el entorno.

## Requisitos

Modo mock:

- ROS2 Jazzy o compatible.
- Python 3.10+.
- `colcon`.

Modo robot:

- NAO V6 con NAOqi activo.
- `naoqi_bridge` y `naoqi_utilities_msgs` disponibles en ROS2.
- Servicios esperados:
  - `/naoqi_speech_node/say`
  - `/naoqi_manipulation_node/go_to_posture`
  - `/naoqi_manipulation_node/play_animation`
  - `/naoqi_navigation_node/move_to`
  - `/naoqi_perception_node/point_at`
  - `/naoqi_miscellaneous_node/toggle_awareness`
  - topico `/set_leds`

## Compilar

Desde esta carpeta:

```bash
bash compile.sh
```

Equivalente:

```bash
colcon build --symlink-install
```

## Ejecutar en modo mock

Este modo no necesita robot. Sirve para probar el flujo completo.

```bash
bash run_mock_stack.sh
```

En otra terminal:

```bash
python3 scripts/send_sample_event.py
```

Deberias ver logs del renderer con acciones tipo:

```text
[MOCK] LEDS FaceLeds ...
[MOCK] SAY ...
[MOCK] PLAY_ANIMATION ...
```

## Ejecutar con robot real

Primero asegurese de que `naoqi_bridge` este conectado al NAO y que los servicios NAOqi existan.

```bash
bash run_robot_stack.sh
```

El bridge HTTP queda escuchando por defecto en:

```text
http://0.0.0.0:8765/robot/feedback
```

Puede cambiar host/puerto con variables de entorno:

```bash
export ROBOT_FEEDBACK_HOST=0.0.0.0
export ROBOT_FEEDBACK_PORT=8765
bash run_robot_stack.sh
```

### Modo interactivo

Por defecto, `robot_stack.launch.py` activa el modo interactivo:

```bash
export NAO_INTERACTIVE_DIALOGUE=true
export NAO_ENABLE_SPEECH_INPUT=true
ros2 launch nao_robot_bringup robot_stack.launch.py
```

En este modo, NAO ya no dice las cuatro preguntas de corrido. El flujo es:

1. DS-Visualizer envia el evento pedagogico.
2. El planner crea una sesion de dialogo en `/nao_dialogue/session`.
3. `nao_dialogue_manager` hace la primera pregunta.
4. El estudiante toca la cabeza de NAO para iniciar grabacion y vuelve a tocar para terminar.
5. `nao_speech_to_text` publica la transcripcion en `/nao_dialogue/student_text`.
6. El manager responde, aclara o avanza a la siguiente pregunta.

Comandos utiles:

```bash
# Desactivar modo interactivo y volver al comportamiento anterior
export NAO_INTERACTIVE_DIALOGUE=false

# Mantener el modo interactivo, pero probar sin microfono
export NAO_ENABLE_SPEECH_INPUT=false
ros2 topic pub --once /nao_dialogue/student_text std_msgs/msg/String "{data: 'no entiendo que significa last'}"
ros2 topic pub --once /nao_dialogue/student_text std_msgs/msg/String "{data: 'continuar'}"
```

El manager usa `google-genai`. Por defecto intenta usar Vertex AI:

```bash
export USE_VERTEX_AI=true
export VERTEX_AI_PROJECT=microservices-459904
export VERTEX_AI_LOCATION=us-central1
export GOOGLE_AI_MODEL=gemini-2.5-flash
```

En modo Vertex AI, las credenciales salen de Google Application Default Credentials. Si Vertex o `google-genai` no estan disponibles, el manager usa un fallback local socratico breve para no bloquear la prueba.

## Contrato de integracion

Ver [docs/CONTRATO_EVENTO_ROBOT.md](docs/CONTRATO_EVENTO_ROBOT.md).

El backend de DS-Visualizer debe enviar, despues de obtener el feedback socratico, un JSON como:

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
    "preguntas": [
      {
        "orden": 1,
        "categoria": "reconocimiento",
        "pregunta": "Despues de add_first, que deberia pasar con first?"
      }
    ],
    "concepto_clave": "Actualizacion de punteros"
  }
}
```

## Mapeo pedagogico del robot

- `CRITICAL_FAILURE`: ojos rojos, tono de pausa, gesto de explicacion.
- `CONCEPT_ERROR`: ojos naranja, pregunta conceptual.
- `PARTIAL_SUCCESS`: ojos amarillos, refuerzo de avance y caso borde.
- `CORRECT_WITH_ISSUES`: ojos verdes, mejora puntual.
- `OPTIMAL`: ojos verdes, refuerzo positivo y aplauso.

## Por que esta arquitectura

NAO no conoce los validadores ni ejecuta logica de estructuras de datos. Solo recibe eventos pedagogicos.
Eso mantiene tres ventajas:

1. El sistema web funciona aunque NAO no este conectado.
2. El robot se puede probar en modo mock.
3. La intervencion fisica queda desacoplada de BET+CLP y del agente socratico.
