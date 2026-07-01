# Recoup Phase 1 container — Cloud Run ready.
# Every deploy-time value is supplied via environment variables (see .env.example);
# nothing (project id, tokens, Stripe keys, billing period) is baked into the image.
#
# Build:  docker build -t recoup .
# Run:    docker run -p 8080:8080 --env-file recoup_agent/.env recoup
# Deploy: gcloud run deploy recoup --source . --set-env-vars GOOGLE_CLOUD_PROJECT=...,...
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Config defaults only — real values come from the environment at runtime.
ENV GOOGLE_GENAI_USE_VERTEXAI=TRUE
ENV PORT=8080

# Serve the Phase 1 FastAPI backend (Firebase auth, multi-tenant, Stripe, pricing).
CMD ["sh", "-c", "uvicorn recoup_agent.api:app --host 0.0.0.0 --port ${PORT}"]
