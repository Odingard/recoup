from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud import secretmanager


class GoogleSecretStore:
    def __init__(self, project: str, client=None):
        self.project = project
        self.client = client if client is not None else secretmanager.SecretManagerServiceClient()

    def _path(self, secret_id: str) -> str:
        return f"projects/{self.project}/secrets/{secret_id}"

    def get(self, secret_id: str) -> str | None:
        try:
            response = self.client.access_secret_version(
                request={"name": f"{self._path(secret_id)}/versions/latest"})
            return response.payload.data.decode("utf-8")
        except NotFound:
            return None

    def put(self, secret_id: str, value: str) -> None:
        try:
            self.client.create_secret(request={
                "parent": f"projects/{self.project}", "secret_id": secret_id,
                "secret": {"replication": {"automatic": {}}},
            })
        except AlreadyExists:
            pass
        self.client.add_secret_version(request={
            "parent": self._path(secret_id), "payload": {"data": value.encode("utf-8")}})

    def delete(self, secret_id: str) -> bool:
        try:
            self.client.delete_secret(request={"name": self._path(secret_id)})
            return True
        except NotFound:
            return False
