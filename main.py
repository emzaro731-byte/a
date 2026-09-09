import json
import os
import re
import time
import uuid
from collections import defaultdict, deque
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agent import run_agent
from db import add_memory, create_conversation, delete_file, delete_memory, get_file, get_messages, init_db, list_conversations, list_files, list_memories, save_file, save_message
from media import router as media_router
from tools import run_tool, tool_catalog

STARTED_AT = time.time()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = os.getenv("AI_MODEL", "qwen2.5:3b")
FAST_MODEL = os.getenv("AI_FAST_MODEL", DEFAULT_MODEL)
REASONING_MODEL = os.getenv("AI_REASONING_MODEL", DEFAULT_MODEL)
CODING_MODEL = os.getenv("AI_CODING_MODEL", DEFAULT_MODEL)
VISION_MODEL = os.getenv("AI_VISION_MODEL", DEFAULT_MODEL)
API_KEY = os.getenv("AI_API_KEY", "")
REQUIRE_API_KEY = os.getenv("REQUIRE_API_KEY", "true").lower() == "true"
RATE_LIMIT = max(0, int(os.getenv("RATE_LIMIT_PER_MINUTE", "60")))
MAX_CONTEXT_MESSAGES = max(1, int(os.getenv("MAX_CONTEXT_MESSAGES", "40")))
SYSTEM_PROMPT = os.getenv("AI_SYSTEM_PROMPT", "You are a helpful, accurate, capable AI assistant. Think carefully, explain clearly, use tools when appropriate, and never invent facts when uncertain.")
ALLOWED_MODELS = {x.strip() for x in os.getenv("ALLOWED_MODELS", ",".join([DEFAULT_MODEL, FAST_MODEL, REASONING_MODEL, CODING_MODEL, VISION_MODEL])).split(",") if x.strip()}

app = FastAPI(title="My AI API", version="6.0.0", description="Self-hosted AI platform with chat, memory, RAG, tools, autonomous agents and media generation.")
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "*").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-User-ID"])
app.include_router(media_router)
_hits: dict[str, deque[float]] = defaultdict(deque)
init_db()

class Message(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Any = Field(min_length=1)

class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[Message] = Field(min_length=1, max_length=200)
    temperature: float = Field(default=0.7, ge=0, le=2)
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    stream: bool = False
    conversation_id: str | None = None
    remember: bool = False
    user_id: str | None = None

class ConversationCreate(BaseModel):
    title: str = Field(default="New chat", min_length=1, max_length=200)
    user_id: str | None = None

class MemoryCreate(BaseModel):
    memory: str = Field(min_length=1, max_length=4000)
    user_id: str | None = None

class ToolRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)

class AgentRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20000)
    model: str | None = None
    user_id: str | None = None


def user_identity(request: Request, supplied: str | None = None) -> str:
    return (supplied or request.headers.get("X-User-ID") or "anonymous")[:200]

def check_api_key(authorization: str | None = Header(default=None)) -> None:
    if REQUIRE_API_KEY and not API_KEY: raise HTTPException(status_code=503, detail="API is not configured with an API key")
    if API_KEY and authorization != f"Bearer {API_KEY}": raise HTTPException(status_code=401, detail="Invalid API key")

def rate_limit(request: Request) -> None:
    if RATE_LIMIT <= 0: return
    key = request.client.host if request.client else "unknown"
    now = time.time(); bucket = _hits[key]
    while bucket and now - bucket[0] > 60: bucket.popleft()
    if len(bucket) >= RATE_LIMIT: raise HTTPException(status_code=429, detail="Rate limit exceeded")
    bucket.append(now)

def resolve_model(name: str | None) -> str:
    model = {None: DEFAULT_MODEL, "default": DEFAULT_MODEL, "fast": FAST_MODEL, "reasoning": REASONING_MODEL, "coding": CODING_MODEL, "vision": VISION_MODEL}.get(name, name)
    if not model or model not in ALLOWED_MODELS: raise HTTPException(status_code=400, detail="Requested model is not allowed")
    return model

def content_to_text(content: Any) -> str:
    if isinstance(content, str): return content
    if isinstance(content, list): return " ".join(str(x.get("text", "")) for x in content if isinstance(x, dict)).strip()
    return str(content)

def enrich(messages: list[dict[str, Any]], uid: str):
    context=[]
    memories=list_memories(uid)
    if memories: context.append("Private long-term memory:\n" + "\n".join(f"- {m['memory']}" for m in memories[:20]))
    query=content_to_text(messages[-1].get("content", ""))
    words={w.lower() for w in re.findall(r"[A-Za-z0-9]{3,}", query)}
    matches=[]
    for f in list_files(uid):
        full=get_file(uid, f["id"])
        text=full.get("content", "") if full else ""
        score=sum(text.lower().count(w) for w in words)
        if score: matches.append((score, f["name"], text))
    for _,name,text in sorted(matches, reverse=True)[:3]: context.append(f"Relevant private file: {name}\n{text[:12000]}")
    return ([{"role":"system","content":"Use this private context only when relevant.\n\n"+"\n\n".join(context)}]+messages) if context else messages

def build_payload(request: ChatRequest, model: str, stream: bool, uid: str):
    messages=[m.model_dump() for m in request.messages]
    if not any(m["role"]=="system" for m in messages) and SYSTEM_PROMPT: messages.insert(0,{"role":"system","content":SYSTEM_PROMPT})
    messages=enrich(messages,uid)
    if len(messages)>MAX_CONTEXT_MESSAGES: messages=messages[:2]+messages[-(MAX_CONTEXT_MESSAGES-2):]
    payload={"model":model,"messages":messages,"stream":stream,"options":{"temperature":request.temperature}}
    if request.max_tokens: payload["options"]["num_predict"]=request.max_tokens
    return payload

@app.middleware("http")
async def security(request: Request, call_next):
    rid=request.headers.get("X-Request-ID") or uuid.uuid4().hex
    if (request.url.path.startswith("/v1/") or request.url.path=="/chat") and request.method!="OPTIONS":
        auth=request.headers.get("Authorization")
        if REQUIRE_API_KEY and not API_KEY: return await _json_error(503,"API is not configured with an API key",rid)
        if API_KEY and auth!=f"Bearer {API_KEY}": return await _json_error(401,"Invalid API key",rid)
    response=await call_next(request); response.headers["X-Request-ID"]=rid; response.headers["X-Content-Type-Options"]="nosniff"; response.headers["X-Frame-Options"]="DENY"; response.headers["Referrer-Policy"]="no-referrer"; return response

async def _json_error(status:int,message:str,rid:str):
    r=JSONResponse(status_code=status,content={"error":{"message":message,"type":"api_error"}}); r.headers["X-Request-ID"]=rid; return r

async def ollama_reachable():
    try:
        async with httpx.AsyncClient(timeout=5) as c: r=await c.get(f"{OLLAMA_URL}/api/tags"); r.raise_for_status(); return True
    except Exception: return False

@app.get("/health")
async def health(): return {"status":"ok" if await ollama_reachable() else "degraded","service":"my-ai-api","version":app.version,"model":DEFAULT_MODEL,"uptime_seconds":round(time.time()-STARTED_AT,1)}
@app.get("/ready")
async def ready():
    if not await ollama_reachable(): raise HTTPException(503,"AI backend is not ready")
    return {"ready":True,"version":app.version}

@app.get("/v1/models",dependencies=[Depends(check_api_key)])
async def models():
    try:
        async with httpx.AsyncClient(timeout=15) as c: data=(await c.get(f"{OLLAMA_URL}/api/tags")).json()
    except Exception as exc: raise HTTPException(503,"AI backend is unavailable") from exc
    return {"object":"list","data":[{"id":x["name"],"object":"model","owned_by":"local"} for x in data.get("models",[]) if x.get("name") in ALLOWED_MODELS]}

@app.get("/v1/config",dependencies=[Depends(check_api_key)])
async def config(): return {"default":DEFAULT_MODEL,"fast":FAST_MODEL,"reasoning":REASONING_MODEL,"coding":CODING_MODEL,"vision":VISION_MODEL,"allowed_models":sorted(ALLOWED_MODELS),"version":app.version}
@app.get("/v1/tools",dependencies=[Depends(check_api_key)])
async def tools(): return {"object":"list","data":tool_catalog()}
@app.post("/v1/tools/run",dependencies=[Depends(check_api_key),Depends(rate_limit)])
async def tools_run(body:ToolRequest):
    try: return {"name":body.name,"result":run_tool(body.name,body.arguments)}
    except Exception as exc: raise HTTPException(400,"Tool execution failed") from exc

@app.post("/v1/agents/run",dependencies=[Depends(check_api_key),Depends(rate_limit)])
async def agent_run(request:Request,body:AgentRequest):
    model=resolve_model(body.model); result=await run_agent(body.task,model,user_identity(request,body.user_id)); return {"model":model,"result":result}

@app.post("/v1/conversations",dependencies=[Depends(check_api_key),Depends(rate_limit)])
async def conversation_create(request:Request,body:ConversationCreate): return {"id":create_conversation(user_identity(request,body.user_id),body.title),"title":body.title}
@app.get("/v1/conversations",dependencies=[Depends(check_api_key)])
async def conversation_list(request:Request): return {"data":list_conversations(user_identity(request))}
@app.get("/v1/conversations/{conversation_id}/messages",dependencies=[Depends(check_api_key)])
async def conversation_messages(conversation_id:str): return {"data":get_messages(conversation_id,MAX_CONTEXT_MESSAGES)}
@app.get("/v1/memories",dependencies=[Depends(check_api_key)])
async def memories(request:Request): return {"data":list_memories(user_identity(request))}
@app.post("/v1/memories",dependencies=[Depends(check_api_key),Depends(rate_limit)])
async def memory_create(request:Request,body:MemoryCreate): add_memory(user_identity(request,body.user_id),body.memory); return {"ok":True}
@app.delete("/v1/memories/{memory_id}",dependencies=[Depends(check_api_key)])
async def memory_delete(request:Request,memory_id:int):
    if not delete_memory(user_identity(request),memory_id): raise HTTPException(404,"Memory not found")
    return {"ok":True}

@app.post("/v1/files",dependencies=[Depends(check_api_key),Depends(rate_limit)])
async def file_upload(request:Request,file:UploadFile=File(...)):
    raw=await file.read()
    if len(raw)>5_000_000: raise HTTPException(413,"File too large; maximum is 5 MB")
    try: text=raw.decode("utf-8")
    except UnicodeDecodeError as exc: raise HTTPException(415,"Only UTF-8 text files are supported") from exc
    return {"id":save_file(user_identity(request),file.filename or "file.txt",file.content_type or "text/plain",text),"name":file.filename,"size":len(raw)}
@app.get("/v1/files",dependencies=[Depends(check_api_key)])
async def files_list(request:Request): return {"data":list_files(user_identity(request))}
@app.get("/v1/files/{file_id}",dependencies=[Depends(check_api_key)])
async def file_get(request:Request,file_id:str):
    item=get_file(user_identity(request),file_id)
    if not item: raise HTTPException(404,"File not found")
    return item
@app.delete("/v1/files/{file_id}",dependencies=[Depends(check_api_key)])
async def file_delete(request:Request,file_id:str):
    if not delete_file(user_identity(request),file_id): raise HTTPException(404,"File not found")
    return {"ok":True}

async def stream_from_ollama(body:ChatRequest,model:str,cid:str,uid:str):
    try:
        async with httpx.AsyncClient(timeout=None) as c:
            async with c.stream("POST",f"{OLLAMA_URL}/api/chat",json=build_payload(body,model,True,uid)) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if not line: continue
                    try: d=json.loads(line)
                    except json.JSONDecodeError: continue
                    text=d.get("message",{}).get("content","")
                    if text: yield f"data: {json.dumps({'id':cid,'object':'chat.completion.chunk','model':model,'choices':[{'index':0,'delta':{'content':text},'finish_reason':None}]})}\n\n"
                    if d.get("done"): yield "data: [DONE]\n\n"
    except Exception: yield f"data: {json.dumps({'error':{'message':'AI model became unavailable','type':'model_error'}})}\n\n"

async def chat_impl(body:ChatRequest,uid:str):
    model=resolve_model(body.model); cid=f"chatcmpl-{uuid.uuid4().hex}"
    if body.stream: return StreamingResponse(stream_from_ollama(body,model,cid,uid),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})
    try:
        async with httpx.AsyncClient(timeout=300) as c: d=(await c.post(f"{OLLAMA_URL}/api/chat",json=build_payload(body,model,False,uid))).json()
    except Exception as exc: raise HTTPException(503,"AI model is unavailable") from exc
    answer=d.get("message",{}).get("content","")
    if body.conversation_id:
        for m in body.messages[-10:]: save_message(body.conversation_id,m.role,content_to_text(m.content))
        save_message(body.conversation_id,"assistant",answer)
    if body.remember and body.messages: add_memory(uid,f"User said: {content_to_text(body.messages[-1].content)[:1000]}")
    p=d.get("prompt_eval_count",0) or 0; q=d.get("eval_count",0) or 0
    return {"id":cid,"object":"chat.completion","model":model,"choices":[{"index":0,"message":{"role":"assistant","content":answer},"finish_reason":"stop"}],"usage":{"prompt_tokens":p,"completion_tokens":q,"total_tokens":p+q}}

@app.post("/v1/chat/completions",dependencies=[Depends(check_api_key),Depends(rate_limit)])
async def chat(request:Request,body:ChatRequest): return await chat_impl(body,user_identity(request,body.user_id))
@app.post("/chat",dependencies=[Depends(check_api_key),Depends(rate_limit)])
async def simple_chat(request:Request,body:ChatRequest): return await chat_impl(body,user_identity(request,body.user_id))
