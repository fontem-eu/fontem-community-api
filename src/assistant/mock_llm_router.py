"""The mock model's HTTP surface: OpenAI chat-completions, scripted.

Served by this app rather than by a service of its own so that the e2e
environments need no extra deployment, image or ArgoCD application to run a
deterministic assistant turn. The cost of that convenience is that the code
ships in the production image, so the gate is explicit and tested: without
``ASSIST_MOCK_MODEL`` the routes are not mounted at all and the model id is
not selectable.

Unauthenticated on purpose. The only caller is this same pod's assistant
turn, reaching it over the cluster-internal service address, and requiring a
token would mean minting one for the agent to call itself. It is reachable
only where the flag is on, it reads nothing and writes nothing, and it can
answer no question that was not already in the request body.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.assistant import mock_llm

router = APIRouter(prefix="/mock-llm", tags=["mock-llm"], include_in_schema=False)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Answer one turn the way the script says.

    Mirrors the subset of the OpenAI contract the engines actually use:
    `messages`, `tools`, `stream`. Anything else in the body is ignored
    rather than rejected — this stands in for a server we do not control,
    and being stricter than the real one would make the mock the thing that
    fails when a client adds a parameter.
    """
    body = await request.json()
    messages = body.get("messages") or []
    model = body.get("model") or mock_llm.MOCK_MODEL_ID
    step = mock_llm.next_step(messages)

    if not body.get("stream"):
        return JSONResponse(mock_llm.completion(step, model))

    def generate():
        yield from mock_llm.stream_chunks(step, model)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.get("/v1/models")
async def models():
    """Some clients list models before using one."""
    return {"object": "list",
            "data": [{"id": mock_llm.MOCK_MODEL_ID, "object": "model",
                      "owned_by": "fontem-e2e"}]}
