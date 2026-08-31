"""mutmut hooks.

Prompt prose is not a contract. The assistant's tool definitions carry long
natural-language `description` strings that the model reads; mutating a word
inside one produces a mutant no test can honestly kill, because pinning that
wording would freeze copy we deliberately keep tuning. Those mutants are
noise that buries the real signal — in the first doc_tools run they were a
large share of the 96 survivors.

Structure is still mutated: names, parameter names, types, required lists,
enums, and every branch of the dispatch logic.
"""


def pre_mutation(context):
    line = context.current_source_line.strip()
    # `description=` / `"description": …` — the value is prompt copy.
    if '"description"' in line or line.startswith('description'):
        context.skip = True
        return
    # A prose fragment of an implicitly-concatenated description block.
    # It opens with a quote and carries no dict braces — that last part is
    # what keeps `"parameters": {"type": "object", …}` (structure, dense
    # with spaces AND colons) from being swallowed. Prose may contain a
    # colon mid-sentence ("about: title, "), so colons alone cannot decide.
    if line.startswith(('"', "'")) and '{' not in line and '}' not in line:
        if ':' not in line or line.count(' ') >= 4:
            context.skip = True
