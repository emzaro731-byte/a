import os
import time
import uuid
from typing import Any, Literal
import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from ai_intelligence import profile_route, select_profile
from production import provider_available, record_failure, record_success, retry_async
from auth import verify_token

router = APIRouter(prefix='/v1/gateway', tags=['model-gateway'])
PROVIDERS = {
 'local': {'base_url': os.getenv('LOCAL_AI_BASE_URL', os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434')).rstrip('/'), 'api_key': os.getenv('LOCAL_AI_API_KEY',''), 'kind':'ollama'},
 'openai': {'base_url': os.getenv('OPENAI_BASE_URL','https://api.openai.com/v1').rstrip('/'), 'api_key': os.getenv('OPENAI_API_KEY',''), 'kind':'openai'},
 'provider2': {'base_url': os.getenv('AI_PROVIDER2_BASE_URL','').rstrip('/'), 'api_key': os.getenv('AI_PROVIDER2_API_KEY',''), 'kind':'openai'},
 'provider3': {'base_url': os.getenv('AI_PROVIDER3_BASE_URL','').rstrip('/'), 'api_key': os.getenv('AI_PROVIDER3_API_KEY',''), 'kind':'openai'},
}
ROUTES = {
 'default': os.getenv('AI_GATEWAY_DEFAULT','local:qwen2.5:3b'), 'fast': os.getenv('AI_GATEWAY_FAST','local:qwen2.5:3b'),
 'reasoning': os.getenv('AI_GATEWAY_REASONING','local:qwen2.5:3b'), 'coding': os.getenv('AI_GATEWAY_CODING','local:qwen2.5:3b'), 'vision': os.getenv('AI_GATEWAY_VISION','local:qwen2.5:3b')}
class GatewayMessage(BaseModel):
 role: Literal['system','user','assistant']; content: Any = Field(min_length=1)
class GatewayRequest(BaseModel):
 model: str|None=None; messages:list[GatewayMessage]=Field(min_length=1,max_length=200); temperature:float=Field(default=.7,ge=0,le=2); max_tokens:int|None=Field(default=None,ge=1,le=32768); stream:bool=False; fallback:bool=True; auto_route:bool=True

def _target(name, messages=None):
 profile=select_profile(name,messages or []) if name and name in {'default','fast','reasoning','coding','vision'} else (name or 'default')
 raw=profile_route(profile) if profile in {'balanced','fast','reasoning','coding','vision'} else ROUTES.get(profile,profile)
 if ':' not in raw: raw=ROUTES['default']
 provider,model=raw.split(':',1)
 if provider not in PROVIDERS or not model: raise HTTPException(400,'Unknown gateway model route')
 if provider!='local' and not PROVIDERS[provider]['base_url']: raise HTTPException(503,f"Provider '{provider}' is not configured")
 return provider,model,profile

def _headers(provider):
 key=PROVIDERS[provider]['api_key']; return {'Authorization':f'Bearer {key}','Content-Type':'application/json'} if key else {'Content-Type':'application/json'}

async def _call(provider,model,body):
 if not provider_available(provider): raise RuntimeError('provider circuit open')
 cfg=PROVIDERS[provider]
 async def op():
  async with httpx.AsyncClient(timeout=300) as client:
   if cfg['kind']=='ollama':
    payload={'model':model,'messages':[m.model_dump() for m in body.messages],'stream':False,'options':{'temperature':body.temperature}}
    if body.max_tokens: payload['options']['num_predict']=body.max_tokens
    response=await client.post(f"{cfg['base_url']}/api/chat",headers=_headers(provider),json=payload)
   else:
    payload={'model':model,'messages':[m.model_dump() for m in body.messages],'temperature':body.temperature,'stream':False}
    if body.max_tokens: payload['max_tokens']=body.max_tokens
    response=await client.post(f"{cfg['base_url']}/chat/completions",headers=_headers(provider),json=payload)
   response.raise_for_status(); return response.json()
 try: data=await retry_async(op,attempts=2); record_success(provider)
 except Exception: record_failure(provider); raise
 if cfg['kind']=='ollama': answer=data.get('message',{}).get('content',''); prompt=data.get('prompt_eval_count',0) or 0; completion=data.get('eval_count',0) or 0
 else:
  choice=(data.get('choices') or [{}])[0]; answer=(choice.get('message') or {}).get('content',''); usage=data.get('usage') or {}; prompt=usage.get('prompt_tokens',0) or 0; completion=usage.get('completion_tokens',0) or 0
 return {'id':f'gw-{uuid.uuid4().hex}','object':'chat.completion','model':f'{provider}:{model}','provider':provider,'choices':[{'index':0,'message':{'role':'assistant','content':answer},'finish_reason':'stop'}],'usage':{'prompt_tokens':prompt,'completion_tokens':completion,'total_tokens':prompt+completion}}

@router.get('/models')
async def gateway_models(): return {'object':'list','data':[{'id':k,'route':v,'profile':k} for k,v in ROUTES.items()]}
@router.get('/providers')
async def gateway_providers(): return {'data':[{'id':n,'configured':bool(c['base_url']) and (n=='local' or bool(c['api_key'])),'available':provider_available(n)} for n,c in PROVIDERS.items()]}
@router.post('/chat/completions')
async def gateway_chat(body:GatewayRequest, authorization:str|None=Header(default=None)):
 api_key=os.getenv('AI_API_KEY',''); authorized=False
 if authorization and authorization.startswith('Bearer '):
  bearer=authorization[7:].strip(); authorized=(bool(api_key) and bearer==api_key) or bool(verify_token(bearer))
 if api_key and not authorized: raise HTTPException(401,'Invalid API key or user token')
 messages=[m.model_dump() for m in body.messages]; provider,model,profile=_target(body.model,messages); started=time.perf_counter()
 try: result=await _call(provider,model,body)
 except Exception as first_error:
  if not body.fallback: raise HTTPException(503,'AI provider is unavailable') from first_error
  fp,fm,_=_target('default',messages)
  if (fp,fm)==(provider,model): raise HTTPException(503,'AI provider is unavailable') from first_error
  try: result=await _call(fp,fm,body); result.update({'fallback_used':True,'fallback_from':f'{provider}:{model}'})
  except Exception as second_error: raise HTTPException(503,'All configured AI providers are unavailable') from second_error
 result.update({'profile':profile,'latency_ms':round((time.perf_counter()-started)*1000,1)}); return result
