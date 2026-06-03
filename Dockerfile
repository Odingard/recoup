# OPTIONAL. The recommended deploy is one command (see README):
#   adk deploy cloud_run --project=$GOOGLE_CLOUD_PROJECT --region=$GOOGLE_CLOUD_LOCATION recoup_agent
# This Dockerfile is here only if you prefer to build/deploy the container yourself.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV GOOGLE_GENAI_USE_VERTEXAI=TRUE
ENV PORT=8080
CMD ["sh", "-c", "adk api_server --host 0.0.0.0 --port ${PORT}"]
