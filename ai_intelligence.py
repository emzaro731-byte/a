import os
import re
from typing import Any

# Capability-aware routing. The gateway can use these profiles without
# pretending that a local model has frontier-model capabilities.
PROFILE_ALIASES = {
    "default": "balanced",
    "fast": "fast",
    "reasoning": "reasoning",
    "coding": "coding",
    "vision": "vision",
}

COMPLEXITY_PATTERNS = [
    ("reasoning", r"\b(prove|derive|debug|architect|analy[sz]e|compare|why|step[- ]by[- ]step|algorithm)\b"),
    ("coding", r"```|\b(code|flutter|dart|python|javascript|typescript|sql|api|github|bug|error|compile)\b"),
    ("vision", r"\b(image|photo|picture|screenshot|diagram|visual)\b"),
]


def classify_task(messages: list[dict[str, Any]]) -> str:
    text = " ".join(str(m.get("content", "")) for m in messages[-6:]).lower()
    for profile, pattern in COMPLEXITY_PATTERNS:
        if re.search(pattern, text, re.I):
            return profile
    if len(text) > int(os.getenv("AI_LONG_TASK_CHARS", "8000")):
        return "reasoning"
    return "balanced"


def select_profile(requested: str | None, messages: list[dict[str, Any]]) -> str:
    if requested and requested in PROFILE_ALIASES:
        return PROFILE_ALIASES[requested]
    return classify_task(messages)


def profile_route(profile: str) -> str:
    env_name = {
        "balanced": "AI_GATEWAY_DEFAULT",
        "fast": "AI_GATEWAY_FAST",
        "reasoning": "AI_GATEWAY_REASONING",
        "coding": "AI_GATEWAY_CODING",
        "vision": "AI_GATEWAY_VISION",
    }.get(profile, "AI_GATEWAY_DEFAULT")
    return os.getenv(env_name, os.getenv("AI_GATEWAY_DEFAULT", "local:qwen2.5:3b"))
