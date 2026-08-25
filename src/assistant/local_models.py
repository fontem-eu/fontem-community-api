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


#: Provider of a model we pay for ourselves, as opposed to llama-server.
#: A turn on one of these spends platform money rather than CPU, which is why
#: `offered()` hides them unless a key is configured.
NEBIUS_PROVIDER = "nebius"
NEBIUS_BASE_URL = "https://api.studio.nebius.com/v1"


@dataclass(frozen=True)
class LocalModel:
    """One offered model. `served_name` is what the provider calls it."""

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
    #: Empty for a model llama-server runs in-cluster. Set to a hosted
    #: provider for one we pay per token for — the turn then leaves the
    #: cluster and costs money, so the two are not interchangeable.
    provider: str = ""

    @property
    def hosted(self) -> bool:
        """Whether a turn on this model spends money rather than CPU."""
        return bool(self.provider)


#: Order matters — this is the order the UI offers them in: the local
#: models fastest-first, then the hosted ones.
#:
#: The two are not one list ranked by speed. A local model's tokens_per_second
#: is measured on our own hardware; a hosted one's is whatever the provider
#: manages under load we do not control, and it bills per token. Sorting them
#: together would rank a paid remote call above a free local one on a number
#: that does not mean the same thing in both halves.
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
    # Hosted. These are last because the list is ordered fastest-first for the
    # UI and these are not the fastest — they are the most capable, and they
    # bill per token.
    #
    # The label carries the provider because the user is choosing where their
    # question goes, not only how good the answer is: a turn on one of these
    # leaves the cluster.
    LocalModel(
        id="qwen3-30b", label="Qwen3 30B A3B [nebius]",
        served_name="Qwen/Qwen3-30B-A3B-Instruct-2507",
        provider=NEBIUS_PROVIDER,
        # Hosted throughput is not ours to measure and varies with their load.
        # The figure is indicative, not a promise like the local ones.
        tokens_per_second=60, context_tokens=131072,
        note="Hosted by Nebius — your question leaves the cluster.",
    ),
    LocalModel(
        id="gpt-oss-120b", label="GPT-OSS 120B [nebius]",
        served_name="openai/gpt-oss-120b",
        provider=NEBIUS_PROVIDER,
        tokens_per_second=45, context_tokens=131072,
        note="Hosted by Nebius — your question leaves the cluster.",
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
    model = _BY_ID.get(wanted)
    if model is None:
        return False
    # A hosted model with no key is not on offer here, and storing a
    # preference for one would leave the user selected on a model that cannot
    # answer — the same reason `offered()` hides it.
    return not model.hosted or bool(hosted_key(model.provider))


def hosted_key(provider: str) -> str:
    """The platform's key for a hosted provider, or "" if none is configured."""
    import os                       # pylint: disable=import-outside-toplevel
    if provider == NEBIUS_PROVIDER:
        return os.environ.get("NEBIUS_API_KEY", "").strip()
    return ""


def offered() -> tuple[LocalModel, ...]:
    """The models this deployment can actually serve.

    A hosted model with no key configured is a dead option: picking it would
    fail every turn. Environments without the secret — a laptop, a fresh
    namespace — therefore see only the local ones, the same way the scripted
    e2e model appears only where it is configured.
    """
    return tuple(m for m in LOCAL_MODELS if not m.hosted or hosted_key(m.provider))


def as_dicts() -> list[dict]:
    """The list handed to the frontend."""
    return [
        {
            "id": m.id,
            "label": m.label,
            "tokens_per_second": m.tokens_per_second,
            "context_tokens": m.context_tokens,
            "note": m.note,
            # The UI shows a different affordance for a model that leaves the
            # cluster; it should not have to parse the label to know.
            "hosted": m.hosted,
        }
        for m in offered()
    ]
