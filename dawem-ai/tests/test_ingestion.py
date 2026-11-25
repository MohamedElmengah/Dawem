"""Offline tests for dawem_ai.ingestion (no network, no model downloads)."""

import tempfile
from pathlib import Path

import pytest

from dawem_ai.exceptions import (
    DocumentLoadError,
    EmptyDocumentError,
    FileNotIndexedError,
    IndexingFailedError,
    OwnershipError,
    UnsupportedFileTypeError,
)
from dawem_ai.ingestion import (
    ChunkRecord,
    IndexingService,
    PageText,
    chunk_pages,
    load_document,
    load_notes,
    make_chunk_id,
)
from tests.fakes import FakeEmbedder, InMemoryChunkStore, make_pdf


def _write(directory: str, name: str, data: bytes) -> Path:
    path = Path(directory) / name
    path.write_bytes(data)
    return path


# ------------------------------- chunker ------------------------------------ #
def test_short_page_is_a_single_chunk_with_its_page_number():
    chunks = chunk_pages([PageText(7, "Short page.")], user_id="u", file_id="f")
    assert [(c.text, c.page_number, c.chunk_index) for c in chunks] == [("Short page.", 7, 0)]


def test_chunks_respect_size_and_never_span_pages():
    pages = [
        PageText(1, " ".join(f"alpha{i}." for i in range(80))),
        PageText(2, " ".join(f"beta{i}." for i in range(80))),
    ]
    chunks = chunk_pages(pages, user_id="u", file_id="f", chunk_size=100, overlap=20)
    assert all(len(c.text) <= 100 for c in chunks)
    for c in chunks:
        marker = "alpha" if c.page_number == 1 else "beta"
        other = "beta" if c.page_number == 1 else "alpha"
        assert marker in c.text and other not in c.text
    assert {c.page_number for c in chunks} == {1, 2}
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_consecutive_chunks_overlap_on_a_whole_sentence():
    text = " ".join(f"Sentence number {i} ends here." for i in range(30))
    chunks = chunk_pages([PageText(None, text)], user_id="u", file_id="f", chunk_size=120, overlap=50)
    assert len(chunks) > 2
    for prev, nxt in zip(chunks, chunks[1:]):
        last_sentence = prev.text.split(". ")[-1].rstrip(".")
        assert last_sentence in nxt.text


def test_unbroken_text_is_hard_split_without_losing_characters():
    blob = "x" * 1234
    chunks = chunk_pages([PageText(1, blob)], user_id="u", file_id="f", chunk_size=100, overlap=10)
    assert all(len(c.text) <= 100 for c in chunks)
    assert "".join(c.text for c in chunks) == blob


def test_arabic_sentences_split_on_arabic_question_mark():
    text = " ".join(f"ما هي الخلية رقم {i}؟" for i in range(40))
    chunks = chunk_pages([PageText(1, text)], user_id="u", file_id="f", chunk_size=80, overlap=20)
    assert len(chunks) > 1
    assert all(len(c.text) <= 80 for c in chunks)
    assert all(c.text.rstrip().endswith("؟") for c in chunks)


def test_chunk_ids_are_deterministic_unique_and_user_scoped():
    pages = [PageText(1, " ".join(f"word{i}." for i in range(200)))]
    a = chunk_pages(pages, user_id="u1", file_id="f", chunk_size=100, overlap=10)
    b = chunk_pages(pages, user_id="u1", file_id="f", chunk_size=100, overlap=10)
    c = chunk_pages(pages, user_id="u2", file_id="f", chunk_size=100, overlap=10)
    assert [x.chunk_id for x in a] == [x.chunk_id for x in b]
    assert len({x.chunk_id for x in a}) == len(a)
    assert not {x.chunk_id for x in a} & {x.chunk_id for x in c}
    assert make_chunk_id("u1", "f", 0) == a[0].chunk_id and len(a[0].chunk_id) == 32


def test_chunker_rejects_bad_parameters():
    with pytest.raises(ValueError):
        chunk_pages([PageText(1, "x")], user_id="u", file_id="f", chunk_size=10)
    with pytest.raises(ValueError):
        chunk_pages([PageText(1, "x")], user_id="u", file_id="f", chunk_size=100, overlap=60)


# ------------------------------- loaders ------------------------------------ #
def test_pdf_pages_are_numbered_physically_and_blank_pages_skipped():
    with tempfile.TemporaryDirectory() as d:
        pdf = _write(d, "bio.pdf", make_pdf(["First page text", "", "Third page text"]))
        pages = load_document(pdf)
    assert [(p.page_number, p.text) for p in pages] == [
        (1, "First page text"),
        (3, "Third page text"),
    ]


def test_scanned_pdf_without_text_layer_raises_empty_document():
    with tempfile.TemporaryDirectory() as d:
        pdf = _write(d, "scan.pdf", make_pdf(["", ""]))
        with pytest.raises(EmptyDocumentError):
            load_document(pdf)


def test_corrupt_pdf_raises_document_load_error():
    with tempfile.TemporaryDirectory() as d:
        pdf = _write(d, "bad.pdf", b"this is definitely not a pdf")
        with pytest.raises(DocumentLoadError):
            load_document(pdf)


def test_text_and_markdown_notes_load_without_page_numbers():
    with tempfile.TemporaryDirectory() as d:
        txt = _write(d, "n.txt", "\ufeffLine one\r\n\r\n\r\n\r\nLine   two".encode("utf-8"))
        md = _write(d, "n.MD", "# Title\ntext".encode("utf-8"))
        assert load_document(txt) == [PageText(None, "Line one\n\nLine two")]
        assert load_document(md)[0].page_number is None


def test_non_utf8_text_file_is_rejected_not_silently_mangled():
    with tempfile.TemporaryDirectory() as d:
        bad = _write(d, "n.txt", "مرحبا".encode("cp1256"))
        with pytest.raises(DocumentLoadError):
            load_document(bad)


def test_unsupported_extension_and_empty_notes():
    with pytest.raises(UnsupportedFileTypeError):
        load_document("slides.pptx")
    with pytest.raises(EmptyDocumentError):
        load_notes("  \n \t ")


# ------------------------------- indexer ------------------------------------ #
BIO = " ".join(f"Photosynthesis step {i} converts light into chemical energy." for i in range(40))


def _service(store=None, embedder=None, sleeps=None, attempts=3):
    sleeps = sleeps if sleeps is not None else []
    return IndexingService(
        store or InMemoryChunkStore(),
        embedder or FakeEmbedder(),
        chunk_size=200,
        chunk_overlap=40,
        max_attempts=attempts,
        backoff_base_s=1.0,
        sleep=sleeps.append,
    )


def test_every_stored_chunk_carries_user_id_file_id_and_source_metadata():
    store = InMemoryChunkStore()
    with tempfile.TemporaryDirectory() as d:
        pdf = _write(d, "tmp-upload-123.pdf", make_pdf(["Cells divide by mitosis", "DNA stores genes"]))
        result = _service(store).index_file(
            pdf, user_id="u1", file_id="f1", file_name="Biology Ch1.pdf"
        )
    assert result.chunk_count == len(store.records) == 2 and result.attempts == 1
    for r in store.records.values():
        assert (r.user_id, r.file_id, r.file_name) == ("u1", "f1", "Biology Ch1.pdf")
        assert r.embedding_model == "fake-hashing-embedder"
    assert sorted(r.page_number for r in store.records.values()) == [1, 2]


def test_chunk_record_rejects_missing_user_or_file_id():
    ok = dict(chunk_id="c", file_name="n", text="t", embedding=[1.0], chunk_index=0)
    ChunkRecord(user_id="u", file_id="f", **ok)
    for bad in ({"user_id": "", "file_id": "f"}, {"user_id": "u", "file_id": " "}):
        with pytest.raises(ValueError):
            ChunkRecord(**bad, **ok)


def test_index_notes_has_no_page_number():
    store = InMemoryChunkStore()
    _service(store).index_notes("Mitochondria make ATP.", user_id="u1", file_id="n1", file_name="notes")
    (record,) = store.records.values()
    assert record.page_number is None


def test_reindexing_replaces_previous_chunks():
    store = InMemoryChunkStore()
    svc = _service(store)
    svc.index_notes(BIO, user_id="u1", file_id="f1", file_name="a")
    long_count = len(store.records)
    svc.index_notes("Short replacement.", user_id="u1", file_id="f1", file_name="a")
    assert long_count > 1 and len(store.records) == 1
    assert next(iter(store.records.values())).text == "Short replacement."


def test_cannot_claim_a_file_id_already_owned_by_another_user():
    store = InMemoryChunkStore()
    svc = _service(store)
    svc.index_notes("Owner's private notes.", user_id="owner", file_id="f1", file_name="a")
    before = dict(store.records)
    with pytest.raises(OwnershipError):
        svc.index_notes("Attacker overwrite.", user_id="mallory", file_id="f1", file_name="b")
    assert store.records == before


def test_delete_file_removes_only_the_owners_chunks_of_that_file():
    store = InMemoryChunkStore()
    svc = _service(store)
    svc.index_notes("Owner notes one.", user_id="u1", file_id="f1", file_name="a")
    svc.index_notes("Owner notes two.", user_id="u1", file_id="f2", file_name="b")
    svc.index_notes("Other user notes.", user_id="u2", file_id="f9", file_name="c")
    svc.delete_file(user_id="u1", file_id="f1")
    assert not store.owns(user_id="u1", file_id="f1")
    assert store.owns(user_id="u1", file_id="f2") and store.owns(user_id="u2", file_id="f9")


def test_delete_file_is_forbidden_for_foreign_and_not_found_for_unknown_files():
    store = InMemoryChunkStore()
    svc = _service(store)
    svc.index_notes("Owner's private notes.", user_id="owner", file_id="f1", file_name="a")
    before = dict(store.records)
    with pytest.raises(OwnershipError):
        svc.delete_file(user_id="mallory", file_id="f1")
    assert store.records == before  # nothing was deleted
    with pytest.raises(FileNotIndexedError):
        svc.delete_file(user_id="owner", file_id="never-indexed")
    with pytest.raises(ValueError):
        svc.delete_file(user_id="", file_id="f1")


def test_missing_ids_are_rejected_up_front():
    with pytest.raises(ValueError):
        _service().index_notes("text", user_id="", file_id="f1", file_name="a")
    with pytest.raises(ValueError):
        _service().index_notes("text", user_id="u1", file_id="  ", file_name="a")


class FlakyEmbedder(FakeEmbedder):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def embed_documents(self, texts):
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("model backend hiccup")
        return super().embed_documents(texts)


def test_transient_embedding_failures_are_retried_with_exponential_backoff():
    sleeps: list[float] = []
    store = InMemoryChunkStore()
    svc = _service(store, FlakyEmbedder(failures=2), sleeps)
    result = svc.index_notes(BIO, user_id="u1", file_id="f1", file_name="a")
    assert result.attempts == 3 and store.records
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] < 1.3 and 2.0 <= sleeps[1] < 2.6


def test_gives_up_after_max_attempts_and_leaves_no_partial_index():
    sleeps: list[float] = []
    store = InMemoryChunkStore()
    svc = _service(store, FlakyEmbedder(failures=99), sleeps, attempts=3)
    with pytest.raises(IndexingFailedError) as info:
        svc.index_notes(BIO, user_id="u1", file_id="f1", file_name="a")
    assert info.value.attempts == 3 and info.value.file_id == "f1"
    assert isinstance(info.value.__cause__, ConnectionError)
    assert len(sleeps) == 2 and not store.records


class HalfWritingStore(InMemoryChunkStore):
    """add() persists half the batch then crashes, `failures` times."""

    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def add(self, records):
        if self.failures > 0:
            self.failures -= 1
            super().add(list(records)[: max(1, len(records) // 2)])
            raise OSError("disk full")
        super().add(records)


def test_partial_store_writes_are_rolled_back_before_retry():
    store = HalfWritingStore(failures=1)
    svc = _service(store)
    result = svc.index_notes(BIO, user_id="u1", file_id="f1", file_name="a")
    assert result.attempts == 2
    expected = len(chunk_pages(load_notes(BIO), user_id="u1", file_id="f1", chunk_size=200, overlap=40))
    assert len(store.records) == expected == result.chunk_count


def test_permanent_errors_are_not_retried():
    sleeps: list[float] = []
    embedder = FakeEmbedder()
    svc = _service(embedder=embedder, sleeps=sleeps)
    with tempfile.TemporaryDirectory() as d:
        with pytest.raises(UnsupportedFileTypeError):
            svc.index_file(_write(d, "a.pptx", b"x"), user_id="u1", file_id="f1")
        with pytest.raises(EmptyDocumentError):
            svc.index_file(_write(d, "scan.pdf", make_pdf([""])), user_id="u1", file_id="f2")
    assert embedder.document_calls == 0 and sleeps == []
