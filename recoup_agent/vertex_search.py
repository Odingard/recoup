"""Optional Vertex AI Search (Discovery Engine) RAG backend for clause retrieval.

If VERTEX_AI_SEARCH_ENGINE_ID is set, `lookup_contract_clause` queries the contract
corpus indexed in Vertex AI Search. If it is not set, or anything fails, the system
falls back to the local clause record - so the demo always runs.

Setup is documented in the README ("Vertex AI Search RAG upgrade").
Written against google-cloud-discoveryengine; could not be executed in the build
sandbox (no GCP), so verify the engine id / location for your project.
"""
from __future__ import annotations
import os


def is_enabled() -> bool:
    return bool(os.environ.get("VERTEX_AI_SEARCH_ENGINE_ID"))


def _client_and_serving_config():
    from google.cloud import discoveryengine_v1 as discoveryengine

    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ.get("VERTEX_AI_SEARCH_LOCATION", "global")
    engine_id = os.environ["VERTEX_AI_SEARCH_ENGINE_ID"]

    client_options = None
    if location != "global":
        from google.api_core.client_options import ClientOptions
        client_options = ClientOptions(api_endpoint=f"{location}-discoveryengine.googleapis.com")

    client = discoveryengine.SearchServiceClient(client_options=client_options)
    serving_config = (
        f"projects/{project}/locations/{location}"
        f"/collections/default_collection/engines/{engine_id}"
        f"/servingConfigs/default_search"
    )
    return discoveryengine, client, serving_config


def search_clause(query: str) -> str | None:
    """Query the contract corpus; return the top extractive passage, or None on failure."""
    if not is_enabled():
        return None
    try:
        discoveryengine, client, serving_config = _client_and_serving_config()
        spec = discoveryengine.SearchRequest.ContentSearchSpec(
            extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                max_extractive_answer_count=1,
                max_extractive_segment_count=1,
            ),
            snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                return_snippet=True,
            ),
        )
        request = discoveryengine.SearchRequest(
            serving_config=serving_config, query=query, page_size=3, content_search_spec=spec,
        )
        response = client.search(request)
        for result in response.results:
            data = result.document.derived_struct_data
            answers = data.get("extractive_answers")
            if answers:
                content = answers[0].get("content")
                if content:
                    return content
            snippets = data.get("snippets")
            if snippets:
                snip = snippets[0].get("snippet")
                if snip:
                    return snip
        return None
    except Exception:
        return None
