"""Self-contained AI assistant module.

This package owns:
  - chat history persistence (its own tables)
  - context budget and history truncation
  - token accounting and usage queries
  - the proxy/LLM client

It deliberately does NOT import from src.domain or src.services beyond
auth primitives. Callers hand it user_id, a conversation_key, a
pre-rendered context blob, and a user message; it returns a stream.
"""
