"""Chat endpoints.

POST /api/chat  {"message": "...", "conversation_id": null, "mode": "auto", "stream": false}
  stream=false -> the Envelope as JSON
  stream=true  -> text/event-stream: meta, status, tool_call, tool_result, token..., final
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.core import Agent
from app.agent.envelope import Envelope
from app.agent.tools import TOOLS, json_schema
from app.api.deps import dataset

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=500)
    conversation_id: str | None = Field(None, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    mode: Literal["auto", "llm", "rules"] = "auto"
    stream: bool = False


def agent(request: Request) -> Agent:
    return request.app.state.agent


def _sse(events):
    for ev in events:
        yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False, default=str)}\n\n"


@router.post("/chat", dependencies=[Depends(dataset)], response_model=Envelope)
def chat(req: ChatRequest, a: Agent = Depends(agent)):
    if req.stream:
        return StreamingResponse(_sse(a.run(req.message, req.conversation_id, req.mode)),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return a.ask(req.message, req.conversation_id, req.mode)


@router.get("/chats")
def recent_chats(a: Agent = Depends(agent)):
    return a.recent()


@router.get("/chat/{conversation_id}")
def history(conversation_id: str, a: Agent = Depends(agent)):
    t = a.transcript(conversation_id)
    if not t:
        raise HTTPException(404, "Unknown conversation")
    return {"conversation_id": conversation_id, "title": t["title"], "turns": t["turns"],
            "messages": a.conversation(conversation_id)}


@router.get("/charts/{chart_id}.png")
def chart_png(chart_id: str):
    """Matplotlib rendering of a chart shown in chat (for WhatsApp / reports)."""
    from fastapi.responses import Response

    from app.agent.charts import STORE, render_png

    c = STORE.get(chart_id)
    if c is None:
        raise HTTPException(404, "Chart expired - ask the question again")
    if "png" not in c:
        c["png"] = render_png(c["title"], c["spec"])
    return Response(c["png"], media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/agent/status")
def agent_status(a: Agent = Depends(agent)):
    return a.status()


@router.get("/agent/tools")
def agent_tools():
    return [{"name": t.name, "description": t.description, "parameters": json_schema(t.args)}
            for t in TOOLS.values()]


@router.get("/research/status")
def research_status(a: Agent = Depends(agent)):
    """Whether web search (Tavily) / page reading (TinyFish) are configured, and today's usage."""
    return a.m.research.status()
