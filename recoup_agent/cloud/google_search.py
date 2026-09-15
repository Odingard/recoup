import os

from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as discoveryengine


class GoogleClauseSearch:
    def __init__(self, client=None):
        project = os.environ["GOOGLE_CLOUD_PROJECT"]
        location = os.environ.get("VERTEX_AI_SEARCH_LOCATION", "global")
        engine_id = os.environ["VERTEX_AI_SEARCH_ENGINE_ID"]
        options = (ClientOptions(api_endpoint=f"{location}-discoveryengine.googleapis.com")
                   if location != "global" else None)
        self.client = client if client is not None else discoveryengine.SearchServiceClient(client_options=options)
        self.serving_config = (
            f"projects/{project}/locations/{location}/collections/default_collection"
            f"/engines/{engine_id}/servingConfigs/default_search")

    def search(self, query: str) -> str | None:
        spec = discoveryengine.SearchRequest.ContentSearchSpec(
            extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                max_extractive_answer_count=1, max_extractive_segment_count=1),
            snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(return_snippet=True))
        request = discoveryengine.SearchRequest(
            serving_config=self.serving_config, query=query, page_size=3, content_search_spec=spec)
        response = self.client.search(request)
        for result in response.results:
            data = result.document.derived_struct_data
            answers = data.get("extractive_answers")
            if answers and answers[0].get("content"):
                return answers[0]["content"]
            snippets = data.get("snippets")
            if snippets and snippets[0].get("snippet"):
                return snippets[0]["snippet"]
        return None
