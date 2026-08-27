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


#: Providers of models we pay for ourselves, as opposed to llama-server.
#: A turn on one of these spends platform money rather than CPU, which is why
#: `offered()` hides them unless a key is configured.
#:
#: Both speak the OpenAI chat-completions protocol at these bases. Adding a
#: third is a row here plus an env var, not another branch.
NEBIUS_PROVIDER = "nebius"
OPENROUTER_PROVIDER = "openrouter"

HOSTED_PROVIDERS: dict[str, dict[str, str]] = {
    NEBIUS_PROVIDER: {
        "base_url": "https://api.studio.nebius.com/v1",
        "env": "NEBIUS_API_KEY",
    },
    OPENROUTER_PROVIDER: {
        "base_url": "https://openrouter.ai/api/v1",
        "env": "OPENROUTER_API_KEY",
    },
}

#: Kept for the call sites that predate HOSTED_PROVIDERS.
NEBIUS_BASE_URL = HOSTED_PROVIDERS[NEBIUS_PROVIDER]["base_url"]


@dataclass(frozen=True)
class LocalModel:  # pylint: disable=too-many-instance-attributes
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
    #: Whether replies open with a reasoning trace before the answer. A
    #: reasoning model needs a reply budget several times a direct
    #: answerer's, or the budget is spent before the first answer token —
    #: measured, not assumed: MiniMax at 900 tokens truncated on every
    #: prompt of an eval run before emitting any answer.
    reasoning: bool = False
    #: Evaluation budgets: the configuration this model would actually be
    #: deployed with, per model rather than one global constant pretending
    #: to fit a 1.7B and a frontier reasoner at once. The eval harness
    #: reads these; CLI flags override them.
    eval_max_rounds: int = 6
    eval_max_tokens: int = 900

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
        note="Hosted by Nebius. Fastest of these, and the least reliable at "
             "opening pages: it never navigated in our evaluation.",
    ),
    LocalModel(
        id="qwen3.5-397b", label="Qwen3.5 397B A17B [nebius]",
        served_name="Qwen/Qwen3.5-397B-A17B",
        provider=NEBIUS_PROVIDER,
        tokens_per_second=40, context_tokens=131072,
        reasoning=True, eval_max_rounds=12, eval_max_tokens=4000,
        note="Hosted by Nebius. Thorough but long-winded: it took 122 tool "
             "calls across our evaluation fixture where the 8B took 15.",
    ),
    LocalModel(
        id="minimax-m3", label="MiniMax M3 [nebius]",
        served_name="MiniMaxAI/MiniMax-M3",
        provider=NEBIUS_PROVIDER,
        tokens_per_second=40, context_tokens=131072,
        reasoning=True, eval_max_rounds=12, eval_max_tokens=4000,
        note="Hosted by Nebius — your question leaves the cluster.",
    ),
    LocalModel(
        id="glm-5.1", label="GLM 5.1 [nebius]",
        served_name="zai-org/GLM-5.1",
        provider=NEBIUS_PROVIDER,
        tokens_per_second=25, context_tokens=131072,
        reasoning=True, eval_max_rounds=12, eval_max_tokens=4000,
        note="Hosted by Nebius. Scored worst of these on sticking to figures "
             "the tools returned.",
    ),
    LocalModel(
        id="ox-alpha", label="Ox Alpha [openrouter]",
        served_name="stealth/ox-alpha",
        provider=OPENROUTER_PROVIDER,
        tokens_per_second=20, context_tokens=1048576,
        reasoning=True, eval_max_rounds=12, eval_max_tokens=4000,
        note="Hosted by OpenRouter, on an undisclosed provider's preview "
             "model. Prompts are shared with them. For evaluation, not "
             "everyday use.",
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


def anonymous_model_id() -> str:
    """The model a signed-out visitor gets. Always a local one.

    ANONYMOUS_MODEL_ID is a constant someone can edit, and the constant alone
    is a thin guard for what it protects: anonymous turns carry no account, no
    metering and no per-user budget, so a hosted model here would let an
    unauthenticated caller spend platform money at whatever rate they can
    issue requests.

    So this does not trust the constant. If it ever names a hosted model — or
    one that no longer exists — the fallback is the first local entry, which
    the ordering makes the smallest. A test pins the constant itself; this is
    what happens if that test is ever deleted along with the mistake.
    """
    model = _BY_ID.get(ANONYMOUS_MODEL_ID)
    if model is not None and not model.hosted:
        return model.id
    return next(m.id for m in LOCAL_MODELS if not m.hosted)


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
    spec = HOSTED_PROVIDERS.get(provider)
    return os.environ.get(spec["env"], "").strip() if spec else ""


def hosted_base_url(provider: str) -> str:
    """Where a turn on this provider is sent. "" for an unknown provider,
    which `resolve_route` treats as "not available" rather than guessing."""
    spec = HOSTED_PROVIDERS.get(provider)
    return spec["base_url"] if spec else ""


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
