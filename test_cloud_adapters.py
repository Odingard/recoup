import ast
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import AlreadyExists, PermissionDenied
from google.cloud import documentai_v1 as documentai
from pydantic import BaseModel, ValidationError
from reportlab.pdfgen.canvas import Canvas

from recoup_agent.cloud.documents import DocumentAiOcr, GeminiOcr, get_document_adapter
from recoup_agent.cloud.google_secrets import GoogleSecretStore
from recoup_agent.cloud.google_search import GoogleClauseSearch
from recoup_agent.cloud.google_storage import FirestoreAdapter
from recoup_agent.cloud.local_documents import LocalDocumentAdapter, _tsv_page
from recoup_agent.cloud.models import GenerationConfig, ProviderConfigurationError, get_model_adapter
from recoup_agent.cloud.remote_models import ModelRequestError
from recoup_agent.cloud.search import get_clause_search
from recoup_agent.cloud.secrets import get_secret_store
from recoup_agent.cloud.storage import get_storage_adapter
from recoup_agent.extraction.extractor import PageAnchoredEntitlement
from recoup_agent.extraction.verifier import verify
from recoup_agent.ingestion_doc import UnreadableDocumentError, extract_entitlements
from recoup_agent.readiness import dependency_checks

ROOT = Path(__file__).parent
TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


class Answer(BaseModel):
    value: int


@pytest.fixture
def remote(monkeypatch):
    calls = []
    result = {"status": 200, "content": '{"value":240}', "finish_reason": "stop"}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(result["status"])
            self.end_headers()
            self.wfile.write(json.dumps({"choices": [{
                "finish_reason": result["finish_reason"],
                "message": {"content": result["content"]},
            }]}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("RECOUP_MODEL_PROVIDER", "remote")
    monkeypatch.setenv("RECOUP_MODEL_URL", f"http://127.0.0.1:{server.server_port}/v1/chat/completions")
    monkeypatch.setenv("RECOUP_REMOTE_MODEL", "local-test-model")
    monkeypatch.delenv("RECOUP_MODEL_API_KEY", raising=False)
    try:
        yield calls, result
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_remote_model_schema_temperature_and_validation(remote):
    calls, result = remote
    adapter = get_model_adapter()
    response = adapter.generate(model="unused-google-default", contents="Read the agreement.",
                                config=GenerationConfig(response_schema=Answer, system_instruction="Use evidence."))
    assert response.parsed == Answer(value=240)
    assert calls[0]["temperature"] == 0
    assert calls[0]["model"] == "local-test-model"
    assert calls[0]["response_format"]["json_schema"]["schema"] == Answer.model_json_schema()
    assert calls[0]["messages"][0] == {"role": "system", "content": "Use evidence."}
    result["content"] = '{"value":"invented"}'
    with pytest.raises(ValidationError):
        adapter.generate(model="unused", contents="Read.", config=GenerationConfig(response_schema=Answer))


def test_remote_failures_are_not_successful_extractions(remote, monkeypatch, tmp_path):
    _, result = remote
    result["finish_reason"] = "length"
    document = tmp_path / "agreement.txt"
    document.write_text("License for 240 seats.")
    monkeypatch.setattr("recoup_agent.extraction.extractor.time.sleep", lambda _: None)
    with pytest.raises(UnreadableDocumentError):
        extract_entitlements(str(document))
    result["finish_reason"] = "stop"
    result["status"] = 503
    with pytest.raises(ModelRequestError) as error:
        get_model_adapter().generate(model="unused", contents="Read.", config=GenerationConfig(response_schema=Answer))
    assert error.value.status_code == 503


def test_remote_cache_unsupported_and_injected_adapters_preserved(remote):
    adapter = get_model_adapter()
    assert get_model_adapter(adapter) is adapter
    with pytest.raises(ProviderConfigurationError):
        adapter.create_cache(model="unused", text="document", system="system", ttl="1800s")


@pytest.mark.parametrize("url", ["http://example.com/api", "file:///etc/passwd", "https://user:password@example.com/api"])
def test_remote_endpoint_validation(monkeypatch, url):
    monkeypatch.setenv("RECOUP_MODEL_PROVIDER", "remote")
    monkeypatch.setenv("RECOUP_MODEL_URL", url)
    monkeypatch.setenv("RECOUP_REMOTE_MODEL", "test")
    with pytest.raises(ProviderConfigurationError):
        get_model_adapter()


def test_document_selection_and_explicit_gemini_rollback(monkeypatch):
    monkeypatch.delenv("RECOUP_OCR_PROVIDER", raising=False)
    monkeypatch.delenv("RECOUP_DOCAI_PROCESSOR", raising=False)
    assert isinstance(get_document_adapter(), LocalDocumentAdapter)
    monkeypatch.setenv("RECOUP_DOCAI_PROCESSOR", "projects/p/locations/eu/processors/id")
    assert isinstance(get_document_adapter(), DocumentAiOcr)
    monkeypatch.setenv("RECOUP_OCR_PROVIDER", "gemini")
    assert isinstance(get_document_adapter(client=SimpleNamespace(models=object())), GeminiOcr)
    monkeypatch.setenv("RECOUP_OCR_PROVIDER", "documentai")
    monkeypatch.setenv("RECOUP_DOCAI_PROCESSOR", " ")
    with pytest.raises(ProviderConfigurationError):
        get_document_adapter()


@pytest.mark.parametrize("confidence", [0.62, 0.97])
def test_documentai_protobuf_confidence_reaches_verification(remote, confidence):
    _, result = remote
    result["content"] = '{"results":[{"index":0,"verdict":"supports"},{"index":1,"verdict":"supports"}]}'
    notice = "Renewal notice must be given sixty days before term end."
    seats = "Customer is licensed for 240 active user seats."
    text = notice + "\n" + seats
    document = documentai.Document(text=text, pages=[{
        "layout": {"text_anchor": {"text_segments": [{"end_index": len(text)}]}},
        "blocks": [
            {"layout": {"confidence": confidence, "text_anchor": {
                "text_segments": [{"end_index": len(notice)}]}}},
            {"layout": {"confidence": 0.99, "text_anchor": {
                "text_segments": [{"start_index": len(notice) + 1, "end_index": len(text)}]}}},
        ],
        "tokens": [{"layout": {"confidence": score}} for score in (0.7, 0.8, 0.9)],
    }])
    client = SimpleNamespace(
        process_document=lambda request: documentai.ProcessResponse(document=document))
    pages = DocumentAiOcr("projects/p/locations/us/processors/p", client=client).page_texts(
        b"scanned-document", "application/pdf")
    assert pages[0].text == text
    assert [block.text for block in pages[0].blocks] == [notice, seats]
    assert pages[0].blocks[0].confidence == pytest.approx(confidence)
    assert pages[0].ocr_confidence == pytest.approx(0.8)
    terms = [
        PageAnchoredEntitlement(term_type="renewal_notice_days", value=60, page=1,
                                provenance=notice, confidence_score=0.95),
        PageAnchoredEntitlement(term_type="committed_seats", value=240, page=1,
                                provenance=seats, confidence_score=0.95),
    ]
    verified = verify(SimpleNamespace(entitlements=terms), pages)
    assert verified[0].verification["ocr_confidence"] == pytest.approx(confidence)
    assert verified[0].verification["ocr_gate"] is (confidence < 0.85)
    assert verified[0].confidence_score == (0.7 if confidence < 0.85 else 0.95)
    assert verified[1].verification["ocr_gate"] is False
    assert verified[1].confidence_score == 0.95


@pytest.mark.parametrize(("variable", "factory"), [
    ("RECOUP_MODEL_PROVIDER", get_model_adapter),
    ("RECOUP_OCR_PROVIDER", get_document_adapter),
    ("RECOUP_STORAGE_PROVIDER", get_storage_adapter),
    ("RECOUP_SECRET_PROVIDER", get_secret_store),
    ("RECOUP_SEARCH_PROVIDER", get_clause_search),
])
def test_unknown_provider_fails_closed(monkeypatch, variable, factory):
    monkeypatch.setenv(variable, "misspelled-provider")
    with pytest.raises(ProviderConfigurationError, match=variable):
        factory()


def test_local_token_confidence_gates_the_affected_term(remote):
    _, result = remote
    result["content"] = '{"results":[{"index":0,"verdict":"supports"},{"index":1,"verdict":"supports"}]}'
    tsv = TSV_HEADER + (
        "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t99\tNotice\n"
        "5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t62\tsixty\n"
        "5\t1\t1\t1\t1\t3\t0\t0\t10\t10\t99\tdays\n"
        "5\t1\t2\t1\t1\t1\t0\t0\t10\t10\t99\tSeats\n"
        "5\t1\t2\t1\t1\t2\t0\t0\t10\t10\t99\t240\n")
    page = _tsv_page(1, tsv)
    assert page.blocks[0].confidence == 0.62
    terms = [PageAnchoredEntitlement(term_type=kind, value=value, confidence_score=0.95,
                                     provenance=quote, page=1)
             for kind, value, quote in [("renewal_notice_days", 60, "Notice sixty days"),
                                       ("committed_seats", 240, "Seats 240")]]
    output = verify(SimpleNamespace(entitlements=terms), [page])
    assert output[0].confidence_score == 0.7
    assert output[0].verification["ocr_gate"] is True
    assert output[1].confidence_score == 0.95
    assert output[1].verification["ocr_gate"] is False


def test_missing_ocr_confidence_is_not_treated_as_clean():
    with pytest.raises(ValueError, match="without confidence"):
        _tsv_page(1, TSV_HEADER + "5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t-1\tNotice\n")


@pytest.mark.skipif(not (shutil.which("tesseract") and shutil.which("pdftoppm")), reason="Local OCR binaries required")
def test_local_pdf_ocr_without_google_clients(monkeypatch):
    monkeypatch.delenv("RECOUP_DOCAI_PROCESSOR", raising=False)
    monkeypatch.setenv("RECOUP_OCR_PROVIDER", "auto")
    data = io.BytesIO()
    canvas = Canvas(data)
    canvas.setFont("Helvetica", 18)
    canvas.drawString(40, 740, "License: 240 seats at $65 per month.")
    canvas.showPage()
    canvas.setFont("Helvetica", 18)
    canvas.drawString(40, 740, "Renewal notice: sixty (60) days.")
    canvas.save()
    pages = get_document_adapter().page_texts(data.getvalue(), "application/pdf")
    assert len(pages) == 2
    assert "240" in pages[0].text and "65" in pages[0].text
    assert "sixty" in pages[1].text and "60" in pages[1].text
    assert all(page.blocks and 0 <= page.ocr_confidence <= 1 for page in pages)


def test_local_missing_binary_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("RECOUP_OCR_PROVIDER", "local")
    monkeypatch.setenv("PATH", "")
    image = tmp_path / "scan.png"
    image.write_bytes(b"not-an-image")
    with pytest.raises(UnreadableDocumentError):
        extract_entitlements(str(image))


def test_local_modules_do_not_import_google_sdks():
    program = """
import importlib.abc
import sys
class NoGoogle(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'google' or fullname.startswith('google.'):
            raise AssertionError('Google SDK import on local path: ' + fullname)
sys.meta_path.insert(0, NoGoogle())
from recoup_agent.cloud.documents import get_document_adapter
from recoup_agent.cloud.models import get_model_adapter
from recoup_agent.cloud.search import get_clause_search
from recoup_agent.cloud.secrets import get_secret_store
from recoup_agent.extraction import load_pages
from recoup_agent.rights_discovery.discovery import generate
assert get_document_adapter().__class__.__name__ == 'LocalDocumentAdapter'
assert get_model_adapter().__class__.__name__ == 'DisabledModelAdapter'
assert get_clause_search().search('clause') is None
assert get_secret_store() is None
"""
    env = {**os.environ, "RECOUP_OCR_PROVIDER": "local", "RECOUP_MODEL_PROVIDER": "disabled",
           "RECOUP_SEARCH_PROVIDER": "local", "RECOUP_SECRET_PROVIDER": "disabled"}
    subprocess.run([sys.executable, "-c", program], check=True, cwd=ROOT, env=env, timeout=30)


def test_google_sdk_imports_stay_in_provider_modules():
    for path in (ROOT / "recoup_agent").rglob("*.py"):
        if "cloud" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                       else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            assert not any(name == "google" or name.startswith(("google.", "vertexai")) for name in modules), path


def test_secret_store_paths_rotation_and_deletion():
    calls = []

    class Client:
        def access_secret_version(self, *, request):
            calls.append(request)
            return SimpleNamespace(payload=SimpleNamespace(data=b"test-token"))

        def create_secret(self, *, request):
            calls.append(request)
            raise AlreadyExists("existing")

        def add_secret_version(self, *, request):
            calls.append(request)

        def delete_secret(self, *, request):
            calls.append(request)

    store = GoogleSecretStore("project", Client())
    assert store.get("recoup-connector-tenant") == "test-token"
    store.put("recoup-connector-tenant", "new-test-token")
    assert store.delete("recoup-connector-tenant")
    assert calls[0]["name"] == "projects/project/secrets/recoup-connector-tenant/versions/latest"
    assert calls[2] == {"parent": "projects/project/secrets/recoup-connector-tenant",
                        "payload": {"data": b"new-test-token"}}
    assert calls[3]["name"] == "projects/project/secrets/recoup-connector-tenant"


def test_secret_permission_error_does_not_write_a_version():
    class Client:
        def create_secret(self, *, request):
            raise PermissionDenied("denied")

        def add_secret_version(self, *, request):
            pytest.fail("permission failures must not be ignored")

    with pytest.raises(PermissionDenied):
        GoogleSecretStore("project", Client()).put("recoup-connector-tenant", "test-token")


def test_storage_create_once_preserves_idempotency():
    class Document:
        def __init__(self):
            self.value = None

        def create(self, data):
            if self.value is not None:
                raise AlreadyExists("duplicate")
            self.value = data

    document = Document()
    store = FirestoreAdapter()
    assert store.create_once(document, {"event": "first"}) is True
    assert store.create_once(document, {"event": "duplicate"}) is False
    assert document.value == {"event": "first"}


def test_search_uses_configured_tenant_independent_engine_and_local_fallback(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project")
    monkeypatch.setenv("VERTEX_AI_SEARCH_ENGINE_ID", "clauses")
    monkeypatch.setenv("VERTEX_AI_SEARCH_LOCATION", "eu")

    class Client:
        def search(self, request):
            assert request.serving_config == (
                "projects/project/locations/eu/collections/default_collection"
                "/engines/clauses/servingConfigs/default_search")
            assert request.query == "Acme clause 4.1"
            return SimpleNamespace(results=[SimpleNamespace(document=SimpleNamespace(
                derived_struct_data={"extractive_answers": [{"content": "240 licensed seats"}]}))])

    assert GoogleClauseSearch(Client()).search("Acme clause 4.1") == "240 licensed seats"
    monkeypatch.setenv("RECOUP_SEARCH_PROVIDER", "local")
    assert get_clause_search().search("Acme clause 4.1") is None


def test_readiness_uses_remote_configuration_without_vertex(remote):
    checks = dependency_checks(deep=False)
    assert checks["model_config"]["ok"] is True
    assert "vertex_config" not in checks
