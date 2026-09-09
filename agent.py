import json
import os
import re
from typing import Any

import httpx

from tools import run_tool, tool_catalog

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
AGENT_MAX_STEPS = max(1, min(8, int(os.getenv("AGENT_MAX_STEPS", "4"))))


def _prompt(task: str, tools: list[dict[str, Any]]) -> str:
    catalog = json.dumps(tools, ensure_ascii=False)
    return f'''You are an autonomous but controlled AI agent. Solve the user's task.
You may call a tool only by returning EXACTLY one JSON object:
{{"action":"tool","name":"TOOL_NAME","arguments":{{...}}}}
If no tool is needed, return EXACTLY:
{{"action":"final","answer":"..."}}
Available tools: {catalog}
User task: {task}'''


async def run_agent(task: str, model: str, user_context: str = "") -> dict[str, Any]:
    tools = tool_catalog()
    transcript = []
    for step in range(AGENT_MAX_STEPS):
        prompt = _prompt(task + ("\nPrivate context:\n" + user_context if user_context else ""), tools)
        if transcript:
            prompt += "\nPrevious agent steps:\n" + "\n".join(transcript)
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False, "options": {"temperature": 0.2}}
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
                r.raise_for_status()
                text = r.json().get("message", {}).get("content", "")
        except Exception as exc:
            return {"status": "error", "step": step + 1, "error": "Agent model unavailable"}
        match = re.search(r"\{\s*\"action\"\s*:\s*\"(?:tool|final)\".*\}", text, re.S)
        if not match:
            return {"status": "completed", "steps": step + 1, "answer": text}
        try:
            action = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {"status": "completed", "steps": step + 1, "answer": text}
        if action.get("action") == "final":
            return {"status": "completed", "steps": step + 1, "answer": str(action.get("answer", ""))}
        if action.get("action") != "tool":
            return {"status": "completed", "steps": step + 1, "answer": text}
        try:
            result = run_tool(str(action.get("name")), action.get("arguments") or {})
        except Exception:
            result = "Tool execution failed"
        transcript.append(json.dumps({"tool": action.get("name"), "result": str(result)[:8000]}, ensure_ascii=False))
    return {"status": "completed", "steps": AGENT_MAX_STEPS, "answer": "I reached the agent step limit before completing the task."}
