"""The built-in models a user may choose between.

Curated rather than discovered. llama-server's router will happily list
whatever GGUF files sit in its directory, and those filenames carry a
quantisation and a build that we change without telling anyone. The id a
user picks is ours; the filename is a mapping.

It also bounds what a caller can ask for. The id travels from the browser,
and "serve whatever the client names" would let anyone load an arbitrary
file out of the model directory.

`label` is the model's actual name, and it is served from here rather than
translated. Model names are proper nouns — "Qwen3 4B" is Qwen3 4B in every
language — so putting them through i18n would only create 24 opportunities
to get a product name wrong.
"""
from dataclasses import dataclass

#: Stable id used in the API and stored against the user. Never a filename.
DEFAULT_MODEL_ID = "qwen3-4b"

#: What a signed-out visitor gets. The smallest model we offer, named
#: explicitly rather than taken as ``LOCAL_MODELS[0]``: that tuple is ordered
#: for the UI ("fastest first"), and a reordering to put a recommended model
#: at the top would silently hand anonymous traffic a bigger one. A test pins
#: this to the smallest of LOCAL_MODELS so the two cannot drift.
#:
#: Anonymous turns are unauthenticated and not metered against an account, so
#: the cheapest model is also the one whose cost an abusive caller cannot run
#: up — and the shared llama-server is memory-bound enough to have been
#: OOMKilled by ordinary load before now.
ANONYMOUS_MODEL_ID = "qwen3-1.7b"


@dataclass(frozen=True)
class LocalModel:
    """One offered model. `served_name` is what llama-server calls it."""

    id: str
    label: str
    served_name: str
    #: Generation speed measured on the current hardware, tokens/sec, so
    #: "faster" is a number the user can see rather than a promise.
    tokens_per_second: int
    #: Usable context. EuroLLM was trained at 4096 and the others far
    #: beyond what we ask for, so this is the one that actually bites.
    context_tokens: int
    #: Shown as a caveat next to the model. Empty for the ones with none.
    note: str = ""


#: Order matters — this is the order the UI offers them in, fastest first.
LOCAL_MODELS: tuple[LocalModel, ...] = (
    LocalModel(
        id="qwen3-1.7b", label="Qwen3 1.7B",
        served_name="qwen3-1.7b-q4_k_m",
        tokens_per_second=28, context_tokens=32768,
    ),
    LocalModel(
        id="qwen3-4b", label="Qwen3 4B",
        served_name="qwen3-4b-q4_k_m",
        tokens_per_second=15, context_tokens=32768,
    ),
    LocalModel(
        id="qwen3-8b", label="Qwen3 8B",
        served_name="qwen3-8b-q4_k_m",
        tokens_per_second=10, context_tokens=32768,
    ),
)

# EuroLLM-9B is deliberately absent, and it was measured rather than
# assumed. It is a fluent multilingual conversationalist — clean answers in
# Portuguese and English at 8.5 tok/s — and it made 0 tool calls out of 20.
# Not "wrong tool", not "sometimes": never.
#
# It has no tool-calling training and its chat template has no tools role,
# so on this platform it cannot search, investigate or navigate. The base
# prompt tells the assistant never to state a figure it did not get from a
# tool call; a model that cannot call tools will not refuse, it will answer
# from memory. Fluent, confident, ungrounded claims about procurement are
# the exact failure this platform exists to fight.
#
# Its context is also 4096 against a ~1300-token system prefix — llama.cpp
# clamps rather than failing, but there is little room left. That is the
# second problem, not the first.

_BY_ID = {m.id: m for m in LOCAL_MODELS}


def resolve(model_id: str | None) -> LocalModel:
    """Map a stored or requested id to a model, falling back to the default.

    Unknown ids fall back rather than raising: a preference can outlive the
    model it names — someone picks one, we retire it — and an assistant
    that refuses to answer because of a stale row is worse than one that
    quietly uses the default.
    """
    return _BY_ID.get((model_id or "").strip().lower(), _BY_ID[DEFAULT_MODEL_ID])


def is_known(model_id: str | None) -> bool:
    """Whether an id is one we offer. Used to reject writes rather than
    silently storing something we will never honour.

    The scripted e2e model counts only where it exists. Anywhere else the id
    is unknown, so it cannot be stored and `resolve` hands back the default
    — a preference row copied from a test environment into production must
    not quietly select a model that is not there.
    """
    wanted = (model_id or "").strip().lower()
    # pylint: disable-next=import-outside-toplevel
    from src.assistant import mock_llm
    if wanted == mock_llm.MOCK_MODEL_ID:
        return mock_llm.enabled()
    return wanted in _BY_ID


def as_dicts() -> list[dict]:
    """The list handed to the frontend."""
    return [
        {
            "id": m.id,
            "label": m.label,
            "tokens_per_second": m.tokens_per_second,
            "context_tokens": m.context_tokens,
            "note": m.note,
        }
        for m in LOCAL_MODELS
    ]
