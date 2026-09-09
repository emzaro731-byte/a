import json
import os
import re
from typing import Any

import httpx

from production import record_failure, record_success, provider_available, retry_async
from tools import run_tool, tool_catalog

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
AGENT_MAX_STEPS = max(1, min(16, int(os.getenv("AGENT_MAX_STEPS", "8"))))


def _prompt(task: str, tools: list[dict[str, Any]], transcript: list[str]) -> str:
    return f'''You are a careful autonomous AI agent. Plan before acting, use the smallest useful set of tools, verify important tool results, and never invent tool output.
Return EXACTLY one JSON object.
For a tool: {{"action":"tool","name":"TOOL_NAME","arguments":{{...}}}}
For completion: {{"action":"final","answer":"..."}}
Available tools: {json.dumps(tools, ensure_ascii=False)}
User task: {task}
Previous steps: {" | ".join(transcript[-8:]) if transcript else "none"}'''


async def _call(model: str, prompt: str) -> str:
    if not provider_available("local"):
        raise RuntimeError("local provider temporarily unavailable")
    async def op():
        async with httpx.AsyncClient(timeout=300) as client:
            r = await client.post(f"{OLLAMA_URL}/api/chat", json={"model": model, "messages":[{"role":"user","content":prompt}],"stream":False,"options":{"temperature":0.15}})
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "")
    try:
        text = await retry_async(op, attempts=3)
        record_success("local")
        return text
    except Exception:
        record_failure("local")
        raise


def _parse(text: str) -> dict[str, Any] | None:
    match = re.search(r"\{\s*\"action\"\s*:\s*\"(?:tool|final)\".*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if data.get("action") in {"tool", "final"} else None


async def run_agent(task: str, model: str, user_context: str = "") -> dict[str, Any]:
    tools = tool_catalog()
    transcript: list[str] = []
    for step in range(AGENT_MAX_STEPS):
        prompt = _prompt(task + ("\nPrivate context:\n" + user_context if user_context else ""), tools, transcript)
        try:
            text = await _call(model, prompt)
        except Exception:
            return {"status":"error","step":step + 1,"error":"Agent model unavailable"}
        action = _parse(text)
        if not action:
            return {"status":"completed","steps":step + 1,"answer":text}
        if action["action"] == "final":
            return {"status":"completed","steps":step + 1,"answer":str(action.get("answer", ""))}
        name = str(action.get("name", ""))
        args = action.get("arguments") or {}
        if name not in {x["name"] for x in tools}:
            transcript.append(json.dumps({"tool":name,"error":"unknown tool"}))
            continue
        try:
            result = run_tool(name, args)
        except Exception as exc:
            result = f"Tool execution failed: {type(exc).__name__}"
        transcript.append(json.dumps({"tool":name,"result":str(result)[:8000]}, ensure_ascii=False))
    return {"status":"completed","steps":AGENT_MAX_STEPS,"answer":"I reached the agent step limit before completing the task."}
