# Recoup Phase 1 container — Cloud Run ready.
# Every deploy-time value is supplied via environment variables (see .env.example);
# nothing (project id, tokens, Stripe keys, billing period) is baked into the image.
#
# Build:  docker build -t recoup .
# Run:    docker run -p 8080:8080 --env-file recoup_agent/.env recoup
# Deploy: gcloud run deploy recoup --source . --set-env-vars GOOGLE_CLOUD_PROJECT=...,...

# --- Stage 1: build the React web app ---
FROM node:20-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ .
RUN VITE_API_BASE=/api npm run build

# --- Stage 2: Python backend serving API + built web app ---
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=web /web/dist web/dist

# Config defaults only — real values come from the environment at runtime.
ENV GOOGLE_GENAI_USE_VERTEXAI=TRUE
ENV PORT=8080

# Serve the Phase 1 FastAPI backend (Firebase auth, multi-tenant, Stripe, pricing).
CMD ["sh", "-c", "uvicorn recoup_agent.api:app --host 0.0.0.0 --port ${PORT}"]
