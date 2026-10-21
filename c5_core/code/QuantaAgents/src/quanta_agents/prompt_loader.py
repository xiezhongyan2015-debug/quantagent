from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from jinja2 import Template
import yaml

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts" / "agents"


@lru_cache(maxsize=None)
def _read_prompt_file(agent_id: str) -> str:
    file_path = PROMPTS_DIR / f"{agent_id}.md"
    return file_path.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def _read_prompt_yaml(agent_id: str) -> dict[str, object]:
    file_path = PROMPTS_DIR / f"{agent_id}.yaml"
    content = file_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)
    if not isinstance(parsed, dict):
        raise ValueError(f"Prompt YAML for '{agent_id}' must be a mapping")
    return parsed


def load_agent_prompt(agent_id: str, section: str, **template_vars: object) -> str:
    """Load a prompt section from an agent prompt file.

    If a YAML prompt file exists, load the section from YAML. Otherwise, fall back to Markdown.
    """
    yaml_path = PROMPTS_DIR / f"{agent_id}.yaml"
    if yaml_path.exists():
        prompts = _read_prompt_yaml(agent_id)
        if section in prompts:
            value = prompts[section]
            text = str(value).strip()
            if template_vars:
                return Template(text).render(**template_vars).strip()
            return text

    content = _read_prompt_file(agent_id)
    pattern = re.compile(r"^##\s+([^\n]+)\s*$", flags=re.MULTILINE)
    matches = list(pattern.finditer(content))

    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        if heading != section:
            continue

        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        extracted = content[start:end].strip()
        if extracted:
            return extracted
        break

    raise ValueError(f"Prompt section '{section}' not found for {agent_id}")


def load_agent_prompt_dict(agent_id: str) -> dict[str, object]:
    yaml_path = PROMPTS_DIR / f"{agent_id}.yaml"
    if yaml_path.exists():
        return _read_prompt_yaml(agent_id)
    raise ValueError(f"Prompt YAML file not found for agent '{agent_id}'")
