from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from soulsaka import models as _models  # noqa: F401  (installs the register warning filter)
from soulsaka.hub.auth import current_device, get_state
from soulsaka.hub.services import chat as chat_svc
from soulsaka.ml.llm import CloudRefused, LLMError

router = APIRouter()


class ChatRequest(BaseModel):
    text: str
    chat_uid: str | None = None
    profile: str | None = None
    mode: Literal["assistant", "twin"] = "assistant"
    register: Literal["text", "email", "speech", "doc"] = "text"
    stream: bool = True


@router.post("/chat")
def chat(req: ChatRequest, request: Request, device=Depends(current_device)):
    state = get_state(request)
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="empty text")
    try:
        result = chat_svc.respond(
            state,
            text=req.text,
            device_uid=device.uid,
            chat_uid=req.chat_uid,
            profile=req.profile,
            mode=req.mode,
            register=req.register,
            stream=req.stream,
        )
    except CloudRefused as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    if not req.stream:
        return result.__dict__

    def gen():
        try:
            for piece in result:
                yield f"event: token\ndata: {json.dumps({'t': piece}, ensure_ascii=False)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except LLMError as e:
            yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@router.get("/chats")
def chats(request: Request, device=Depends(current_device)):
    return chat_svc.list_chats(get_state(request))


@router.get("/chats/{chat_uid}")
def chat_detail(chat_uid: str, request: Request, device=Depends(current_device)):
    return chat_svc.chat_turns(get_state(request), chat_uid)


@router.get("/llm/profiles")
def profiles(request: Request, device=Depends(current_device)):
    llm = get_state(request).service("llm")
    return llm.list_profiles()


@router.get("/llm/profiles/{name}/available")
def profile_available(name: str, request: Request, device=Depends(current_device)):
    llm = get_state(request).service("llm")
    return {"name": name, "available": llm.available(name)}
