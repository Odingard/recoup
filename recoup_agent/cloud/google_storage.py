import os

from google.api_core.exceptions import AlreadyExists
from google.cloud import firestore


class FirestoreAdapter:
    def client(self):
        project = os.getenv("GOOGLE_CLOUD_PROJECT")
        return firestore.Client(project=project) if project else firestore.Client()

    def equal(self, collection, field, value):
        return collection.where(filter=firestore.FieldFilter(field, "==", value)).stream()

    def transact(self, transaction, operation):
        return firestore.transactional(operation)(transaction)

    def create_once(self, document, data):
        try:
            document.create(data)
            return True
        except AlreadyExists:
            return False
