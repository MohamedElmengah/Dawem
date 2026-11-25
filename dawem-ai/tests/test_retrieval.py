"""Offline tests: user isolation, ownership validation, metadata, and the 422 gate.

Isolation tests run against the in-memory reference store always, and against real
ChromaDB (EphemeralClient, caller-supplied vectors, no model download) when chromadb is
installed - that is the run that proves the actual `where` filters.
"""

import tempfile
import uuid
from pathlib import Path

import pytest

from dawem_ai.exceptions import (
    INSUFFICIENT_CONTEXT_DETAIL,
    FileNotIndexedError,
    InsufficientContextError,
    OwnershipError,
)
from dawem_ai.ingestion import IndexingService, ScoredChunk
from dawem_ai.retrieval import Retriever
from tests.fakes import FakeEmbedder, InMemoryChunkStore, make_pdf

PHOTO = "Photosynthesis converts sunlight, water and carbon dioxide into glucose and oxygen in chloroplasts."
PHOTO_QUERY = "photosynthesis converts sunlight water carbon dioxide into glucose oxygen"
REVOLUTION = "The French Revolution began in 1789 and abolished the monarchy and feudal privileges."


def _chroma_store():
    chromadb = pytest.importorskip("chromadb")
    from dawem_ai.ingestion import ChromaChunkStore

    # EphemeralClient state is shared in-process: use a unique collection per test.
    return ChromaChunkStore(
        client=chromadb.EphemeralClient(), collection_name=f"t_{uuid.uuid4().hex}"
    )


STORES = [pytest.param(InMemoryChunkStore, id="memory"), pytest.param(_chroma_store, id="chroma")]


def _world(make_store, threshold=0.35):
    """u1 owns f1 (photosynthesis) + f2 (revolution); u2 owns f9 with IDENTICAL text to f1."""
    store, embedder = make_store(), FakeEmbedder()
    indexer = IndexingService(store, embedder, chunk_size=200, chunk_overlap=40, sleep=lambda s: None)
    indexer.index_notes(PHOTO, user_id="u1", file_id="f1", file_name="Biology.md")
    indexer.index_notes(REVOLUTION, user_id="u1", file_id="f2", file_name="History.md")
    indexer.index_notes(PHOTO, user_id="u2", file_id="f9", file_name="Other user's bio.md")
    return store, embedder, Retriever(store, embedder, top_k=8, relevance_threshold=threshold)


# --------------------------- user isolation --------------------------------- #
@pytest.mark.parametrize("make_store", STORES)
def test_search_only_returns_the_requesting_users_chunks(make_store):
    _, _, retriever = _world(make_store)
    mine = retriever.retrieve(user_id="u2", file_ids=["f9"], query=PHOTO_QUERY)
    assert mine and {c.file_id for c in mine} == {"f9"}

    theirs = retriever.retrieve(user_id="u1", file_ids=["f1"], query=PHOTO_QUERY)
    assert theirs and {c.file_id for c in theirs} == {"f1"}
    # identical text, but never the same chunk across users
    assert not {c.chunk_id for c in mine} & {c.chunk_id for c in theirs}


@pytest.mark.parametrize("make_store", STORES)
def test_store_query_is_filtered_by_user_even_when_file_id_is_foreign(make_store):
    """Defense in depth: bypass the retriever's ownership check, hit the store directly."""
    store, embedder, _ = _world(make_store)
    hits = store.query(embedder.embed_query(PHOTO), user_id="u2", file_ids=["f1"], top_k=10)
    assert hits == []


@pytest.mark.parametrize("make_store", STORES)
def test_file_ids_filter_restricts_the_search_scope(make_store):
    _, _, retriever = _world(make_store, threshold=0.0)  # no relevance gate: isolate the filter
    only_history = retriever.retrieve(user_id="u1", file_ids=["f2"], query="photosynthesis")
    assert {c.file_id for c in only_history} == {"f2"}
    both = retriever.retrieve(user_id="u1", file_ids=["f1", "f2"], query="photosynthesis")
    assert {c.file_id for c in both} == {"f1", "f2"}


# --------------------------- ownership validation --------------------------- #
@pytest.mark.parametrize("make_store", STORES)
def test_foreign_file_is_rejected_even_when_mixed_with_owned_files(make_store):
    _, embedder, retriever = _world(make_store)
    with pytest.raises(OwnershipError) as info:
        retriever.retrieve(user_id="u2", file_ids=["f9", "f1"], query="photosynthesis")
    assert (info.value.user_id, info.value.file_id) == ("u2", "f1")
    assert embedder.query_calls == 0  # rejected before any embedding work


@pytest.mark.parametrize("make_store", STORES)
def test_unknown_file_is_not_found_while_a_foreign_one_is_forbidden(make_store):
    # Project documentation s.17: 404 = does not exist, 403 = not owned by this user.
    _, embedder, retriever = _world(make_store)
    with pytest.raises(FileNotIndexedError) as info:
        retriever.retrieve(user_id="u1", file_ids=["does-not-exist"], query="photosynthesis")
    assert (info.value.user_id, info.value.file_id) == ("u1", "does-not-exist")
    assert not isinstance(info.value, OwnershipError)
    with pytest.raises(OwnershipError):
        retriever.retrieve(user_id="u1", file_ids=["f9"], query="photosynthesis")  # f9 is u2's
    assert embedder.query_calls == 0


def test_input_validation_is_a_caller_bug_not_a_422():
    retriever = Retriever(InMemoryChunkStore(), FakeEmbedder())
    for kwargs in (
        dict(user_id="", file_ids=["f"], query="q"),
        dict(user_id="u", file_ids=[], query="q"),
        dict(user_id="u", file_ids=[" "], query="q"),
        dict(user_id="u", file_ids=["f"], query="   "),
    ):
        with pytest.raises(ValueError):
            retriever.retrieve(**kwargs)
    with pytest.raises(ValueError):
        Retriever(InMemoryChunkStore(), FakeEmbedder(), relevance_threshold=1.5)
    with pytest.raises(ValueError):
        Retriever(InMemoryChunkStore(), FakeEmbedder(), top_k=0)


# --------------------------- metadata preservation -------------------------- #
@pytest.mark.parametrize("make_store", STORES)
def test_source_metadata_survives_the_round_trip(make_store):
    store, embedder = make_store(), FakeEmbedder()
    indexer = IndexingService(store, embedder, sleep=lambda s: None)
    with tempfile.TemporaryDirectory() as d:
        pdf = Path(d) / "upload-abc.pdf"
        pdf.write_bytes(make_pdf(["Intro text about cells", "", "Mitosis divides the nucleus"]))
        indexer.index_file(pdf, user_id="u1", file_id="f1", file_name="Cell Biology.pdf")
    indexer.index_notes("Ribosomes build proteins", user_id="u1", file_id="n1", file_name="My notes")
    retriever = Retriever(store, embedder, relevance_threshold=0.1)

    (hit,) = retriever.retrieve(user_id="u1", file_ids=["f1"], query="mitosis nucleus")[:1]
    assert hit.page_number == 3  # physical page; the blank page 2 was skipped
    assert hit.file_name == "Cell Biology.pdf"
    assert hit.file_id == "f1" and len(hit.chunk_id) == 32 and "Mitosis" in hit.text
    assert 0.1 <= hit.score <= 1.0

    (note,) = retriever.retrieve(user_id="u1", file_ids=["n1"], query="ribosomes proteins")
    assert note.page_number is None and note.file_name == "My notes"


@pytest.mark.parametrize("make_store", STORES)
def test_results_are_ordered_best_first(make_store):
    _, _, retriever = _world(make_store, threshold=0.0)
    chunks = retriever.retrieve(user_id="u1", file_ids=["f1", "f2"], query="photosynthesis sunlight glucose")
    scores = [c.score for c in chunks]
    assert scores == sorted(scores, reverse=True) and chunks[0].file_id == "f1"


# --------------------------- 422 grounding gate ----------------------------- #
@pytest.mark.parametrize("make_store", STORES)
def test_irrelevant_query_raises_insufficient_context(make_store):
    _, _, retriever = _world(make_store, threshold=0.35)
    with pytest.raises(InsufficientContextError) as info:
        retriever.retrieve(user_id="u1", file_ids=["f1", "f2"], query="quantum chromodynamics gluon confinement")
    err = info.value
    assert str(err) == INSUFFICIENT_CONTEXT_DETAIL
    assert err.retrieved > 0 and err.threshold == 0.35
    assert err.best_score is not None and err.best_score < 0.35


class StubStore:
    """Returns canned hits; lets tests control scores/ownership precisely."""

    def __init__(self, hits, owns=True):
        self.hits, self._owns = hits, owns

    def owns(self, *, user_id, file_id):
        return self._owns

    def query(self, embedding, *, user_id, file_ids, top_k):
        return list(self.hits)


def _hit(chunk_id, score, user_id="u1", file_id="f1"):
    return ScoredChunk(chunk_id=chunk_id, user_id=user_id, file_id=file_id,
                       file_name="n", text="t", score=score, page_number=1)


def test_threshold_boundary_keeps_equal_and_drops_below():
    store = StubStore([_hit("high", 0.9), _hit("edge", 0.5), _hit("low", 0.499)])
    retriever = Retriever(store, FakeEmbedder(), relevance_threshold=0.5)
    chunks = retriever.retrieve(user_id="u1", file_ids=["f1"], query="q")
    assert [c.chunk_id for c in chunks] == ["high", "edge"]


def test_all_hits_below_threshold_raises_with_best_score():
    retriever = Retriever(StubStore([_hit("a", 0.2), _hit("b", 0.34)]), FakeEmbedder(), relevance_threshold=0.35)
    with pytest.raises(InsufficientContextError) as info:
        retriever.retrieve(user_id="u1", file_ids=["f1"], query="q")
    assert info.value.retrieved == 2 and info.value.best_score == 0.34


def test_no_hits_at_all_raises_insufficient_context():
    retriever = Retriever(StubStore([]), FakeEmbedder())
    with pytest.raises(InsufficientContextError) as info:
        retriever.retrieve(user_id="u1", file_ids=["f1"], query="q")
    assert info.value.retrieved == 0 and info.value.best_score is None


def test_leaked_foreign_chunks_from_a_buggy_store_are_dropped():
    leaky = StubStore([
        _hit("other-user", 0.99, user_id="u2"),
        _hit("other-file", 0.98, file_id="f-not-requested"),
        _hit("mine", 0.6),
    ])
    retriever = Retriever(leaky, FakeEmbedder(), relevance_threshold=0.35)
    assert [c.chunk_id for c in retriever.retrieve(user_id="u1", file_ids=["f1"], query="q")] == ["mine"]

    only_leaks = StubStore([_hit("other-user", 0.99, user_id="u2")])
    with pytest.raises(InsufficientContextError):
        Retriever(only_leaks, FakeEmbedder()).retrieve(user_id="u1", file_ids=["f1"], query="q")
