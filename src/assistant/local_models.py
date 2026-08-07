"""The built-in models a user may choose between.

Curated rather than discovered. llama-server's router will happily list
whatever GGUF files sit in its directory, but those filenames are an
operational detail: they carry a quantisation, a version, and a size that
we change without telling anyone. A stored preference has to outlive that,
so the id a user picks is ours and the filename is a mapping.

It also bounds what a caller can ask for. The model name travels from the
browser, and "serve whatever the client names" would let anyone load an
arbitrary file from the model directory.
"""
from dataclasses import dataclass

#: Stable id used in the API and stored against the user. Never a filename.
DEFAULT_MODEL_ID = "balanced"


@dataclass(frozen=True)
class LocalModel:
    """One offered model. `served_name` is what llama-server calls it."""

    id: str
    served_name: str
    #: Rough generation speed on the current hardware, tokens/sec. Shown to
    #: the user so "faster" is a number rather than a promise.
    tokens_per_second: int


#: Order matters — this is the order the UI offers them in.
LOCAL_MODELS: tuple[LocalModel, ...] = (
    LocalModel(id="fast", served_name="qwen3-1.7b-q4_k_m", tokens_per_second=32),
    LocalModel(id="balanced", served_name="qwen3-4b-q4_k_m", tokens_per_second=16),
)

_BY_ID = {m.id: m for m in LOCAL_MODELS}


def resolve(model_id: str | None) -> LocalModel:
    """Map a stored or requested id to a model, falling back to the default.

    Unknown ids fall back rather than raising: a preference can outlive the
    model it names — someone picks `fast`, we retire it — and an assistant
    that refuses to answer because of a stale row is worse than one that
    quietly uses the default.
    """
    return _BY_ID.get((model_id or "").strip().lower(), _BY_ID[DEFAULT_MODEL_ID])


def is_known(model_id: str | None) -> bool:
    """Whether an id is one we offer. Used to reject writes rather than
    silently storing something we will never honour."""
    return (model_id or "").strip().lower() in _BY_ID


def as_dicts() -> list[dict]:
    """The list handed to the frontend."""
    return [
        {"id": m.id, "tokens_per_second": m.tokens_per_second}
        for m in LOCAL_MODELS
    ]
