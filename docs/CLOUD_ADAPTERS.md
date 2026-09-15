# Cloud adapters

The API's document, model, persistence, secret and search integrations live in
`recoup_agent/cloud/`. Financial calculation and recovery approval rules remain in
the domain modules; providers cannot set finding amounts, approve recovery or
charge fees.

## Configuration

| Variable | Default | Supported values |
| --- | --- | --- |
| `RECOUP_OCR_PROVIDER` | `auto` | `auto`, `documentai`, `local`, `gemini` |
| `RECOUP_MODEL_PROVIDER` | `google` | `google`, `remote`, `disabled` |
| `RECOUP_STORAGE_PROVIDER` | `firestore` | `firestore` |
| `RECOUP_SECRET_PROVIDER` | `google` | `google`, `disabled` |
| `RECOUP_SEARCH_PROVIDER` | `auto` | `auto`, `google`, `local` |

Unknown provider names raise configuration errors. Restart the process after
changing provider configuration; persistence and secret clients are cached.
No provider falls back to another cloud after a request fails.

Production retains Document AI when `RECOUP_DOCAI_PROCESSOR` is set, Google model
extraction, Firestore and Secret Manager with existing tenant paths, transaction
semantics and IAM. Search `auto` uses Vertex Search only when
`VERTEX_AI_SEARCH_ENGINE_ID` is set, otherwise the existing local clause lookup.
The existing image still deploys through Artifact Registry to Cloud Run.

### Local OCR

With an empty `RECOUP_DOCAI_PROCESSOR`, OCR `auto` selects Tesseract and Poppler
inside the container. This changes the previous automatic Gemini OCR fallback.
Set `RECOUP_OCR_PROVIDER=gemini` to opt back into it.

Local OCR sends no document bytes to an external OCR service. It accepts PDFs
(1–25 pages), PNGs and JPEGs. Text-layer PDFs, DOCX, TXT and Markdown keep their
existing local text extraction. Poppler renders PDFs at 150 DPI with a 2400-pixel
maximum dimension. Temporary raster files are removed on success or failure.
`RECOUP_LOCAL_OCR_TIMEOUT_SECONDS` caps the whole OCR operation (default 120).
`RECOUP_LOCAL_OCR_LANGUAGES` defaults to `eng`; install additional Tesseract
language packs in the image before configuring them.

Tesseract word confidence is normalized to 0–1. Each block carries the minimum
word confidence; each page carries mean word confidence. The existing verifier
uses cited block confidence to hold affected terms below
`RECOUP_OCR_CONFIDENCE_GATE` (default 0.85). Missing binaries, unreadable documents,
missing word confidence and OCR timeouts produce an ingestion error; they never
become successful empty extractions. OCR confidence is an engine estimate, not
a guarantee that transcription is correct.

Document AI preserves its block-layout and mean-token confidence. Explicit
Gemini OCR still has no OCR confidence signal. All paths retain the existing
quote verification and conflict gates.

For a non-container development environment:

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-eng poppler-utils
```

### Model providers

Google model SDK calls, binary parts, structured-generation settings, timeouts,
context-cache creation/deletion and response conversion are confined to
`google_models.py`. The extraction, verification and rights-discovery modules use
`ModelAdapter` with a provider-neutral schema, response and content types.
Existing injected Gemini-shaped clients remain supported for callers/tests.

`remote` targets a Chat Completions endpoint that supports JSON-schema output:

```bash
export RECOUP_OCR_PROVIDER=local
export RECOUP_MODEL_PROVIDER=remote
export RECOUP_MODEL_URL=http://127.0.0.1:8000/v1/chat/completions
export RECOUP_REMOTE_MODEL=your-served-model-name
# Set RECOUP_MODEL_API_KEY through your secret manager if the endpoint requires it.
```

The endpoint must be HTTPS except on loopback. Redirects are rejected. The
configured model replaces Gemini model defaults for extraction, verification and
rights discovery. Calls use temperature 0 and validate returned JSON against the
requested Pydantic schema. Incomplete outputs and refusals fail closed; retriable
HTTP errors preserve their status code for the existing retry policy. Caching is
unsupported, so long-document extraction uses its existing chunked fallback.
This adapter is text-only: use local/Document AI OCR, not the legacy binary
single-shot extractor or Gemini OCR, with a remote model.

Local OCR alone does **not** make model processing local. Google remains the
default model provider. Use a locally hosted remote endpoint for local model
processing, or `disabled` to reject model extraction entirely. No model server or
weights are bundled into Recoup's image.

## Portability boundaries

`CloudDocumentAdapter`, `ModelAdapter`, `StorageAdapter`, `SecretStore` and
`ClauseSearch` define integration boundaries. A new provider implements the
corresponding protocol and is registered in its factory; vendor SDK types stay
in its provider module. Monetary math, normalized terms, tenant-scoped repository
operations and approval logic do not need provider-specific changes.

This release supplies Google providers, local OCR, and a remote model protocol.
It does not supply an AWS/Azure database or secret-store implementation, migrate
existing data, or make the whole application independent of Google. Firebase
authentication remains unchanged. The optional `adk web`/`adk run` entry point
uses the isolated Google ADK provider and explicitly requires the Google model
provider. API model selection does not alter that optional agent runner.

## Verification

```bash
python -m pip install -r requirements-dev.txt
ruff check recoup_agent/cloud test_cloud_adapters.py
env -u RECOUP_BILLING_STRIPE_API_KEY -u RECOUP_CONNECTOR_TEST_STRIPE_API_KEY pytest -q
(cd web && npm run lint && npm run build)
docker build -t recoup-adapters .
```

Adapter tests exercise local PDF OCR, remote HTTP/schema contracts, fail-closed
errors, per-term confidence gating, secret rotation and idempotent writes. An
import guard verifies local document processing does not load Google SDKs; an
AST check prevents Google SDK imports outside provider modules. Existing
reconciliation, tenant isolation, approval and realization tests remain intact.
