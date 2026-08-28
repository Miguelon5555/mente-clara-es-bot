"""
generate.py — Mente Clara ES bot (v2: series + Shorts)

Modos:
  python generate.py long    -> genera el próximo video largo de la serie activa
  python generate.py short   -> genera el próximo Short de la serie activa

Mantiene el progreso en data/series_state.json (serie activa, índice de video
largo, índice de short). Cuando se agotan los videos largos de una serie,
avanza automáticamente a la siguiente serie de config/topics.json y reinicia
los índices. El Short se repite en bucle sobre shorts_pool si la serie dura
más semanas que elementos tiene el pool.

Variables de entorno requeridas:
- ANTHROPIC_API_KEY
- GITHUB_TOKEN
- GITHUB_REPOSITORY   (formato "usuario/repo", GitHub Actions lo define solo)
"""

import json
import os
import sys
from pathlib import Path

import requests
from anthropic import Anthropic

CONFIG_DIR = Path(__file__).parent / "config"
DATA_DIR = Path(__file__).parent / "data"
TOPICS_PATH = CONFIG_DIR / "topics.json"
SYSTEM_PROMPT_PATH = CONFIG_DIR / "system_prompt.md"
STATE_PATH = DATA_DIR / "series_state.json"

CLAUDE_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4000


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def load_state(num_series: int) -> dict:
    if STATE_PATH.exists():
        return load_json(STATE_PATH)
    # Estado inicial si es la primera ejecución del bot
    return {"series_index": 0, "long_form_index": 0, "short_index": 0}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def advance_series_if_needed(data: dict, state: dict) -> dict:
    series_list = data["series"]
    current_series = series_list[state["series_index"] % len(series_list)]

    if state["long_form_index"] >= len(current_series["long_form_topics"]):
        # Serie terminada: avanzar a la siguiente y reiniciar índices
        state["series_index"] = (state["series_index"] + 1) % len(series_list)
        state["long_form_index"] = 0
        state["short_index"] = 0
        current_series = series_list[state["series_index"]]
        print(f"Serie completada. Nueva serie activa: {current_series['series_name']}")

    return current_series


def build_user_message(title: str, pillar: str, fmt: str, duration: int, disclaimer: str, series_name: str) -> str:
    return (
        f"Genera el guion completo para este video del canal Mente Clara ES.\n\n"
        f"Serie: {series_name}\n"
        f"Título: {title}\n"
        f"Pilar: {pillar}\n"
        f"Formato: {fmt}\n"
        f"Duración objetivo: {duration} minutos\n\n"
        f"Recuerda incluir el disclaimer de forma natural en algún punto: \"{disclaimer}\"\n"
        f"Sigue exactamente la estructura definida en tus instrucciones para este formato."
    )


def generate_script(system_prompt: str, user_message: str) -> str:
    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return "".join(block.text for block in message.content if block.type == "text")


def create_github_issue(title: str, pillar: str, fmt: str, duration: int, series_name: str,
                         suggested_time: str, script_body: str, is_short: bool) -> None:
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]

    url = f"https://api.github.com/repos/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    kind_label = "short" if is_short else "video-largo"
    payload = {
        "title": f"[{series_name}] [{kind_label}] {title}",
        "body": (
            f"**Serie:** {series_name}\n"
            f"**Pilar:** {pillar}\n"
            f"**Formato:** {fmt}\n"
            f"**Duración objetivo:** {duration} min\n"
            f"**Horario sugerido de publicación:** {suggested_time}\n\n"
            f"---\n\n{script_body}"
        ),
        "labels": ["guion-pendiente-revision", pillar, kind_label],
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    print(f"Issue creado: {response.json()['html_url']}")


def run_long_form(data: dict, state: dict) -> None:
    series = advance_series_if_needed(data, state)
    topic = series["long_form_topics"][state["long_form_index"]]
    system_prompt = load_text(SYSTEM_PROMPT_PATH)
    disclaimer = data.get("disclaimer", "")
    suggested_time = data["publish_schedule"]["long_form"]["suggested_time_local"]

    user_message = build_user_message(
        topic["title"], topic["pillar"], topic["format"], topic["target_duration_min"],
        disclaimer, series["series_name"]
    )
    script_body = generate_script(system_prompt, user_message)
    create_github_issue(
        topic["title"], topic["pillar"], topic["format"], topic["target_duration_min"],
        series["series_name"], suggested_time, script_body, is_short=False
    )

    state["long_form_index"] += 1


def run_short(data: dict, state: dict) -> None:
    series = advance_series_if_needed(data, state)
    pool = series["shorts_pool"]
    short_title = pool[state["short_index"] % len(pool)]  # repite en bucle si se agota el pool
    system_prompt = load_text(SYSTEM_PROMPT_PATH)
    disclaimer = data.get("disclaimer", "")
    suggested_time = data["publish_schedule"]["shorts"]["suggested_time_local"]

    user_message = build_user_message(
        short_title, "shorts", "short", 1, disclaimer, series["series_name"]
    )
    script_body = generate_script(system_prompt, user_message)
    create_github_issue(
        short_title, "shorts", "short", 1, series["series_name"], suggested_time,
        script_body, is_short=True
    )

    state["short_index"] += 1


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("long", "short"):
        print("Uso: python generate.py [long|short]", file=sys.stderr)
        sys.exit(1)

    data = load_json(TOPICS_PATH)
    state = load_state(len(data["series"]))

    try:
        if sys.argv[1] == "long":
            run_long_form(data, state)
        else:
            run_short(data, state)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    save_state(state)


if __name__ == "__main__":
    main()
