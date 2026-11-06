import sys

import pytest
from huri_rag import ingestion, qdrant_utils


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
