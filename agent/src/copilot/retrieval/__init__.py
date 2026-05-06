"""Hybrid retrieval over the clinical guideline corpus.

Three modules:

- ``migrate`` — DB migration (pgvector extension + ``guideline_chunks`` table).
- ``corpus`` — PDF text extraction + section-aware chunking. Pure functions,
  no I/O outside the input filesystem; produces the chunk records that the
  indexer ships to Postgres.
- ``indexer`` — orchestrates corpus → Cohere embeddings → idempotent INSERT
  into pgvector. Wired into the deploy pipeline (issue 012).

The retriever (issue 008) lives in ``retrieval/retriever.py`` and is a
sibling module — it consumes the same table this package indexes into.
"""
