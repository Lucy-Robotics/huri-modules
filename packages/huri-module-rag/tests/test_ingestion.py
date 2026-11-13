import builtins
import sys
import uuid
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import cast

import numpy as np
import pytest
from huri_rag import ingestion, qdrant_utils
from qdrant_client import QdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue


@pytest.mark.parametrize(
    ("verify_ssl", "expected_client_verify"),
    [(None, True), (True, True), (False, False)],
)
def test_remote_embedder_passes_ssl_verification_to_client(
    monkeypatch, verify_ssl, expected_client_verify
):
    client_options = {}

    def make_client(**kwargs):
        client_options.update(kwargs)
        return object()

    monkeypatch.setattr(ingestion.httpx, "Client", make_client)

    if verify_ssl is None:
        ingestion.RemoteEmbedder("https://embedding.example/", "model")
    else:
        ingestion.RemoteEmbedder(
            "https://embedding.example/", "model", verify_ssl=verify_ssl
        )

    assert client_options == {"timeout": 60.0, "verify": expected_client_verify}


@pytest.mark.parametrize(
    ("ssl_option", "expected_verify_ssl"),
    [(None, True), ("--no-verify-ssl", False)],
)
def test_cli_passes_ssl_setting_to_remote_embedder(
    monkeypatch, ssl_option, expected_verify_ssl
):
    observed = {}

    class FakeRemoteEmbedder:
        def __init__(self, url, model_name, verify_ssl=True):
            observed["embedding_verify_ssl"] = verify_ssl

    def make_qdrant_client(url, verify_ssl):
        observed["qdrant_verify_ssl"] = verify_ssl
        return object()

    def run_text(args, client, model, user_id):
        observed["model"] = model

    argv = [
        "ingestion",
        "--user-id",
        "test-user",
        "--embedding-url",
        "https://embedding.example",
    ]
    if ssl_option is not None:
        argv.append(ssl_option)
    argv.extend(["text", "unused.txt"])

    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(ingestion, "get_user_id", lambda user_id: user_id)
    monkeypatch.setattr(ingestion, "RemoteEmbedder", FakeRemoteEmbedder)
    monkeypatch.setattr(ingestion, "cmd_text", run_text)
    monkeypatch.setattr(qdrant_utils, "make_qdrant_client", make_qdrant_client)

    ingestion.main()

    assert observed["embedding_verify_ssl"] is expected_verify_ssl
    assert observed["qdrant_verify_ssl"] is expected_verify_ssl
    assert isinstance(observed["model"], FakeRemoteEmbedder)


def test_remote_embedder_normalizes_the_returned_vector(monkeypatch):
    observed = {}

    class Response:
        def raise_for_status(self):
            observed["raised"] = True

        def json(self):
            return {"data": [{"embedding": [3.0, 4.0]}]}

    class Client:
        def post(self, url, json):
            observed["url"] = url
            observed["json"] = json
            return Response()

    client = Client()
    monkeypatch.setattr(ingestion.httpx, "Client", lambda **_kwargs: client)
    embedder = ingestion.RemoteEmbedder("https://embedding.example/", "model")

    vector = embedder.encode("hello")

    np.testing.assert_allclose(vector, [0.6, 0.8])
    assert vector.dtype == np.float32
    assert observed == {
        "raised": True,
        "url": "https://embedding.example/v1/embeddings",
        "json": {"model": "model", "input": "hello"},
    }


@pytest.mark.parametrize(
    ("embedding", "normalize", "expected"),
    [([0.0, 0.0], True, [0.0, 0.0]), ([3.0, 4.0], False, [3.0, 4.0])],
)
def test_remote_embedder_handles_zero_and_unnormalized_vectors(
    monkeypatch, embedding, normalize, expected
):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"embedding": embedding}]}

    class Client:
        def post(self, *_args, **_kwargs):
            return Response()

    client = Client()
    monkeypatch.setattr(ingestion.httpx, "Client", lambda **_kwargs: client)
    embedder = ingestion.RemoteEmbedder("https://embedding.example", "model")

    np.testing.assert_allclose(
        embedder.encode("hello", normalize_embeddings=normalize), expected
    )


def test_sentence_splitting_and_fixed_chunks_preserve_overlap():
    assert ingestion._split_sentences(" First.\n\nSecond!  Third? ") == [
        "First.",
        "Second!",
        "Third?",
    ]
    assert ingestion.chunk_text(
        "one two. three four. five six.", chunk_size=3, overlap=2
    ) == ["one two.", "one two. three four.", "three four. five six."]
    assert ingestion.chunk_text("  \n\n", chunk_size=3) == []


def test_extract_text_from_pdf_joins_pages(monkeypatch):
    pages = [
        SimpleNamespace(extract_text=lambda: "first"),
        SimpleNamespace(extract_text=lambda: "second"),
    ]
    module = ModuleType("pypdf")
    setattr(module, "PdfReader", lambda _path: SimpleNamespace(pages=pages))
    monkeypatch.setitem(sys.modules, "pypdf", module)

    assert ingestion.extract_text_from_pdf("example.pdf") == "first\nsecond"


def test_extract_text_from_pdf_reports_missing_dependency(monkeypatch, capsys):
    original_import = builtins.__import__

    def import_without_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("pypdf is unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_pypdf)

    with pytest.raises(SystemExit) as error:
        ingestion.extract_text_from_pdf("example.pdf")

    assert error.value.code == 1
    assert "Install a PDF library" in capsys.readouterr().out


def test_get_user_id_uses_provided_file_and_generates_when_empty(monkeypatch, tmp_path):
    user_file = tmp_path / "user_id"
    monkeypatch.setattr(ingestion, "USER_ID_FILE", str(user_file))
    assert ingestion.get_user_id("provided") == "provided"
    assert not user_file.exists()

    user_file.write_text("saved-user\n", encoding="utf-8")
    assert ingestion.get_user_id() == "saved-user"

    user_file.write_text("  \n", encoding="utf-8")
    generated = uuid.UUID(int=1)
    monkeypatch.setattr(ingestion.uuid, "uuid4", lambda: generated)
    assert ingestion.get_user_id() == str(generated)
    assert user_file.read_text(encoding="utf-8") == str(generated)


def test_ensure_collection_creates_only_missing_collection():
    class Client:
        def __init__(self):
            self.created = []

        def get_collections(self):
            return SimpleNamespace(collections=[SimpleNamespace(name="existing")])

        def create_collection(self, **kwargs):
            self.created.append(kwargs)

    fake_client = Client()
    client = cast(QdrantClient, fake_client)
    ingestion.ensure_collection(client, "existing", 3)
    assert fake_client.created == []

    ingestion.ensure_collection(client, "new", 3)
    assert fake_client.created[0]["collection_name"] == "new"
    assert fake_client.created[0]["vectors_config"].size == 3


def test_ingest_chunks_batches_points_and_handles_empty_input():
    class Model:
        def encode(self, text, normalize_embeddings):
            assert normalize_embeddings is True
            return np.asarray([len(text), 1.0], dtype=np.float32)

    class Client:
        def __init__(self):
            self.batches = []

        def upsert(self, **kwargs):
            self.batches.append(kwargs)

    fake_client = Client()
    client = cast(QdrantClient, fake_client)
    count = ingestion.ingest_chunks(
        client, Model(), "docs", ["x"] * 101, "user", source="notes.txt"
    )

    assert count == 101
    assert [len(batch["points"]) for batch in fake_client.batches] == [100, 1]
    payload = fake_client.batches[0]["points"][0].payload
    assert payload["text"] == "x"
    assert payload["_user_id"] == "user"
    assert payload["source"] == "notes.txt"
    assert payload["chunk_index"] == 0

    empty_fake_client = Client()
    empty_client = cast(QdrantClient, empty_fake_client)
    assert (
        ingestion.ingest_chunks(
            empty_client, Model(), "docs", [], "user", source="empty.txt"
        )
        == 0
    )
    assert empty_fake_client.batches == []


def test_chunk_strategy_uses_fixed_and_semantic_paths(monkeypatch):
    fixed_args = SimpleNamespace(chunking="fixed", chunk_size=2, overlap=0)
    assert ingestion.chunk_strat("one two. three.", fixed_args, None) == [
        "one two.",
        "one two. three.",
    ]

    observed: dict[str, object] = {}
    module = ModuleType("huri_rag.semantic_chunker")

    class FakeSemanticChunker:
        def __init__(self, model, strategy):
            observed["model"] = model
            observed["strategy"] = strategy

        def chunk(self, text):
            observed["text"] = text
            return ["semantic"]

    setattr(module, "SemanticChunker", FakeSemanticChunker)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    semantic_args = SimpleNamespace(chunking="semantic", semantic_strategy="threshold")
    model = object()

    assert ingestion.chunk_strat("source", semantic_args, model) == ["semantic"]
    assert observed == {
        "model": model,
        "strategy": "threshold",
        "text": "source",
    }


def test_cmd_pdf_skips_non_pdf_and_processes_textless_and_readable_pdfs(
    monkeypatch, tmp_path, capsys
):
    nested = tmp_path / "nested"
    nested.mkdir()
    direct_pdf = tmp_path / "direct.PDF"
    nested_pdf = nested / "nested.pdf"
    non_pdf = tmp_path / "notes.txt"
    direct_pdf.touch()
    nested_pdf.touch()
    non_pdf.touch()
    ingested: list[str] = []

    class Model:
        def encode(self, *_args, **_kwargs):
            return np.zeros(3)

    monkeypatch.setattr(ingestion, "ensure_collection", lambda *_args: None)
    monkeypatch.setattr(
        ingestion,
        "extract_text_from_pdf",
        lambda path: "content" if Path(path).name == direct_pdf.name else " ",
    )
    monkeypatch.setattr(ingestion, "chunk_strat", lambda *_args: ["chunk"])

    def fake_ingest_chunks(*_args: object, **kwargs: object) -> int:
        source = kwargs["source"]
        assert isinstance(source, str)
        ingested.append(source)
        return 1

    monkeypatch.setattr(ingestion, "ingest_chunks", fake_ingest_chunks)
    args = SimpleNamespace(
        files=[str(direct_pdf), str(nested), str(non_pdf)], collection="docs"
    )

    ingestion.cmd_pdf(args, object(), Model(), "user")

    assert ingested == [direct_pdf.name]
    output = capsys.readouterr().out
    assert "Skipping non-PDF" in output
    assert "No text extracted" in output
    assert "Total: 1 chunks from 2 PDF(s)" in output


def test_cmd_pdf_reports_when_no_pdf_is_found(capsys, tmp_path):
    ingestion.cmd_pdf(
        SimpleNamespace(files=[str(tmp_path / "notes.txt")]),
        object(),
        object(),
        "user",
    )
    assert "No PDF files found." in capsys.readouterr().out


def test_cmd_text_skips_missing_and_empty_files_and_ingests_content(
    monkeypatch, tmp_path, capsys
):
    empty = tmp_path / "empty.txt"
    full = tmp_path / "full.txt"
    empty.write_text("  ", encoding="utf-8")
    full.write_text("read me", encoding="utf-8")
    ingested: list[tuple[list[str], str]] = []

    class Model:
        def encode(self, *_args, **_kwargs):
            return np.zeros(2)

    monkeypatch.setattr(ingestion, "ensure_collection", lambda *_args: None)
    monkeypatch.setattr(ingestion, "chunk_strat", lambda text, *_args: [text])

    def fake_ingest_chunks(
        _client: object,
        _model: object,
        _collection: str,
        chunks: list[str],
        *_args: object,
        **kwargs: object,
    ) -> int:
        source = kwargs["source"]
        assert isinstance(source, str)
        ingested.append((chunks, source))
        return len(chunks)

    monkeypatch.setattr(ingestion, "ingest_chunks", fake_ingest_chunks)
    args = SimpleNamespace(
        files=[str(tmp_path / "missing.txt"), str(empty), str(full)],
        collection="docs",
    )

    ingestion.cmd_text(args, object(), Model(), "user")

    assert ingested == [(["read me"], full.name)]
    output = capsys.readouterr().out
    assert "File not found" in output
    assert "File is empty" in output
    assert "Total: 1 chunks from 3 file(s)" in output


def test_cmd_write_returns_for_empty_text(monkeypatch, capsys):
    lines = iter(["  "])

    def read_line():
        try:
            return next(lines)
        except StopIteration:
            raise EOFError

    monkeypatch.setattr(builtins, "input", read_line)
    ingestion.cmd_write(
        SimpleNamespace(title="empty", collection="docs"),
        object(),
        object(),
        "user",
    )
    assert "Nothing to ingest." in capsys.readouterr().out


def test_cmd_write_ingests_text_until_eof(monkeypatch):
    lines = iter(["first", "second"])

    def read_line():
        try:
            return next(lines)
        except StopIteration:
            raise EOFError

    class Model:
        def encode(self, *_args, **_kwargs):
            return np.zeros(2)

    observed: dict[str, object] = {}
    monkeypatch.setattr(builtins, "input", read_line)
    monkeypatch.setattr(ingestion, "ensure_collection", lambda *_args: None)
    monkeypatch.setattr(ingestion, "chunk_strat", lambda text, *_args: [text])

    def fake_ingest_chunks(
        _client: object,
        _model: object,
        _collection: str,
        chunks: list[str],
        _user: str,
        **kwargs: object,
    ) -> int:
        observed.update(chunks=chunks, **kwargs)
        return len(chunks)

    monkeypatch.setattr(ingestion, "ingest_chunks", fake_ingest_chunks)

    ingestion.cmd_write(
        SimpleNamespace(title="meeting", collection="docs"),
        object(),
        Model(),
        "user",
    )

    assert observed == {
        "chunks": ["first\nsecond"],
        "source": "meeting",
        "doc_type": "manual",
    }


def test_cmd_profile_requires_facts_and_replaces_existing_profile(monkeypatch):
    delete_calls: list[tuple[str, Filter]] = []
    ingest_calls: list[tuple[list[str], dict[str, str]]] = []

    class Client:
        def delete(self, collection_name: str, points_selector: Filter) -> None:
            delete_calls.append((collection_name, points_selector))

    class Model:
        def encode(self, *_args, **_kwargs):
            return np.zeros(2)

    monkeypatch.setattr(ingestion, "ensure_collection", lambda *_args: None)

    def fake_ingest_chunks(
        _client: object,
        _model: object,
        _collection: str,
        chunks: list[str],
        _user: str,
        **kwargs: object,
    ) -> int:
        source = kwargs["source"]
        doc_type = kwargs["doc_type"]
        assert isinstance(source, str)
        assert isinstance(doc_type, str)
        ingest_calls.append((chunks, {"source": source, "doc_type": doc_type}))
        return len(chunks)

    monkeypatch.setattr(ingestion, "ingest_chunks", fake_ingest_chunks)
    client = Client()
    ingestion.cmd_profile(
        SimpleNamespace(collection="docs", name=None, fact=[]),
        client,
        Model(),
        "user",
    )
    assert delete_calls == []
    assert ingest_calls == []

    ingestion.cmd_profile(
        SimpleNamespace(collection="docs", name="Ada", fact=["likes tea"]),
        client,
        Model(),
        "user",
    )
    assert delete_calls[0][0] == "docs"
    selector = delete_calls[0][1]
    must = selector.must
    assert isinstance(must, list)
    assert isinstance(must[0], FieldCondition)
    assert isinstance(must[0].match, MatchValue)
    assert must[0].match.value == "user"
    assert isinstance(must[1], FieldCondition)
    assert isinstance(must[1].match, MatchValue)
    assert must[1].match.value == "profile"
    assert ingest_calls[0] == (
        ["The user's name is Ada.", "likes tea"],
        {"source": "profile", "doc_type": "profile"},
    )


def test_cmd_list_handles_missing_empty_and_grouped_collections(capsys):
    class Client:
        def __init__(self, points=None, error=False):
            self.points = points or []
            self.error = error

        def get_collection(self, _collection):
            if self.error:
                raise RuntimeError("missing")
            return SimpleNamespace(points_count=len(self.points))

        def scroll(self, **_kwargs):
            return self.points, None

    args = SimpleNamespace(collection="docs")
    ingestion.cmd_list(args, Client(error=True), None, "user")
    assert "doesn't exist" in capsys.readouterr().out

    ingestion.cmd_list(args, Client(), None, "user")
    assert "No documents found" in capsys.readouterr().out

    points = [
        SimpleNamespace(payload={"source": "b", "type": "text"}),
        SimpleNamespace(payload={"source": "a", "type": "pdf"}),
        SimpleNamespace(payload={}),
    ]
    ingestion.cmd_list(args, Client(points), None, "user")
    output = capsys.readouterr().out
    assert "Total: 3 chunks across 3 sources" in output
    assert "unknown" in output


def test_cmd_delete_requires_source_then_deletes_matching_user_and_source(
    capsys,
):
    class Client:
        def __init__(self):
            self.calls: list[tuple[str, Filter]] = []

        def delete(self, collection_name: str, points_selector: Filter) -> None:
            self.calls.append((collection_name, points_selector))

    client = Client()
    ingestion.cmd_delete(SimpleNamespace(source=""), client, None, "user")
    assert client.calls == []
    assert "Specify --source" in capsys.readouterr().out

    ingestion.cmd_delete(
        SimpleNamespace(source="notes.txt", collection="docs"),
        client,
        None,
        "user",
    )
    assert client.calls[0][0] == "docs"
    selector = client.calls[0][1]
    must = selector.must
    assert isinstance(must, list)
    assert isinstance(must[0], FieldCondition)
    assert isinstance(must[0].match, MatchValue)
    assert must[0].match.value == "user"
    assert isinstance(must[1], FieldCondition)
    assert isinstance(must[1].match, MatchValue)
    assert must[1].match.value == "notes.txt"


def test_main_does_not_load_embedding_model_for_list(monkeypatch):
    observed_qdrant: tuple[str, bool] | None = None
    observed_command: tuple[str, object, object | None, str] | None = None

    def make_client(url: str, verify_ssl: bool) -> object:
        nonlocal observed_qdrant
        observed_qdrant = (url, verify_ssl)
        return object()

    def run_list(
        args: SimpleNamespace,
        client: object,
        model: object | None,
        user_id: str,
    ) -> None:
        nonlocal observed_command
        observed_command = (args.command, client, model, user_id)

    monkeypatch.setattr(sys, "argv", ["ingestion", "--user-id", "u", "list"])
    monkeypatch.setattr(ingestion, "get_user_id", lambda value: value)
    monkeypatch.setattr(qdrant_utils, "make_qdrant_client", make_client)
    monkeypatch.setattr(ingestion, "cmd_list", run_list)

    ingestion.main()

    assert observed_qdrant == ("http://localhost:6333", True)
    assert observed_command is not None
    assert observed_command[0] == "list"
    assert observed_command[2] is None
    assert observed_command[3] == "u"


def test_main_rejects_remote_semantic_chunking(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ingestion",
            "--user-id",
            "u",
            "--embedding-url",
            "https://embedding.example",
            "--chunking",
            "semantic",
            "text",
            "unused.txt",
        ],
    )

    with pytest.raises(SystemExit) as error:
        ingestion.main()

    assert error.value.code == 2


def test_main_loads_local_sentence_transformer_lazily(monkeypatch):
    observed: dict[str, object] = {}
    module = ModuleType("sentence_transformers")

    class FakeSentenceTransformer:
        def __init__(self, name):
            observed["model_name"] = name

    setattr(module, "SentenceTransformer", FakeSentenceTransformer)
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ingestion",
            "--user-id",
            "u",
            "--embedding-model",
            "local-model",
            "profile",
            "--name",
            "Ada",
        ],
    )
    monkeypatch.setattr(ingestion, "get_user_id", lambda value: value)
    monkeypatch.setattr(qdrant_utils, "make_qdrant_client", lambda *_args: object())
    monkeypatch.setattr(
        ingestion,
        "cmd_profile",
        lambda _args, _client, model, _user: observed.update(model=model),
    )

    ingestion.main()

    assert observed["model_name"] == "local-model"
    assert isinstance(observed["model"], FakeSentenceTransformer)


def _run_cmd_pdf_on_folder(monkeypatch, folder):
    ingested: list[str] = []

    class Model:
        def encode(self, *_args, **_kwargs):
            return np.zeros(3)

    monkeypatch.setattr(ingestion, "ensure_collection", lambda *_args: None)
    monkeypatch.setattr(ingestion, "extract_text_from_pdf", lambda _path: "content")
    monkeypatch.setattr(ingestion, "chunk_strat", lambda *_args: ["chunk"])

    def fake_ingest_chunks(*_args: object, **kwargs: object) -> int:
        source = kwargs["source"]
        assert isinstance(source, str)
        ingested.append(source)
        return 1

    monkeypatch.setattr(ingestion, "ingest_chunks", fake_ingest_chunks)
    args = SimpleNamespace(files=[str(folder)], collection="docs")
    ingestion.cmd_pdf(args, object(), Model(), "user")
    return ingested


def test_cmd_pdf_folder_finds_pdfs_whatever_the_extension_case(monkeypatch, tmp_path):
    (tmp_path / "Facture-2025-03.PDF").touch()
    (tmp_path / "contrat.pdf").touch()
    sub = tmp_path / "2024"
    sub.mkdir()
    (sub / "Releve.Pdf").touch()
    (tmp_path / "todo.txt").touch()

    ingested = _run_cmd_pdf_on_folder(monkeypatch, tmp_path)

    assert sorted(ingested) == ["Facture-2025-03.PDF", "Releve.Pdf", "contrat.pdf"]


def test_cmd_pdf_folder_without_pdf_says_so(monkeypatch, tmp_path, capsys):
    (tmp_path / "todo.txt").touch()

    ingested = _run_cmd_pdf_on_folder(monkeypatch, tmp_path)

    assert ingested == []
    assert "No PDF files found." in capsys.readouterr().out
