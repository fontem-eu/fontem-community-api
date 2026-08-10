"""Who decides whether `propose_edit` is on the table.

The server used to infer it from `bool(context_block)` — whether the article
had any TEXT. That is a different question from whether there is somewhere to
propose into, and the two disagree in the one case that matters most: a story
that has just been created has no body yet. The tool was withdrawn at exactly
the moment the user asked for their first paragraph, and the model, offered no
way to propose, went looking through the entity graph instead.

Measured against staging with an identical prompt and model, varying only this
field: empty context_block called propose_edit 0/4, non-empty 1/1. It read for
a long time as the model being too small — first the 1.7B, then the 4B, both
of which call the tool reliably when they are actually given it.

The client holds the report id and the editor state, and needs both to apply a
proposal, so it is the only party that can answer the question.
"""
from src.assistant.service import ChatRequest


def _payload_has_editor(**kwargs) -> bool:
    """The one expression under test, as `turn` computes it."""
    req = ChatRequest(
        user_id="u", conversation_key="report:1", message="add a paragraph",
        context_block=kwargs.pop("context_block", ""), **kwargs,
    )
    return (bool(req.context_block) if req.has_editor is None
            else bool(req.has_editor))


def test_empty_article_with_an_open_editor_still_gets_propose_edit():
    """The regression. A new story has no text and every right to an edit."""
    assert _payload_has_editor(context_block="", has_editor=True) is True


def test_client_saying_no_editor_withdraws_the_tool():
    """Reading an article is not editing one.

    The assistant is on every page, so this is the common case, and offering
    a tool whose result cannot be applied produces a card that does nothing.
    """
    assert _payload_has_editor(
        context_block="a long article body", has_editor=False) is False


def test_older_client_falls_back_to_inferring_from_the_context_block():
    """Clients that predate the field must keep working.

    Not a good signal — it is the bug above — but it is strictly better than
    treating a missing field as False, which would withdraw the tool from
    every older client instead of just the empty-article case.
    """
    assert _payload_has_editor(context_block="an article body") is True
    assert _payload_has_editor(context_block="") is False


def test_the_flag_wins_over_the_text_in_both_directions():
    """Whichever way they disagree, the client's answer is the one used."""
    assert _payload_has_editor(context_block="", has_editor=True) is True
    assert _payload_has_editor(context_block="text", has_editor=False) is False
