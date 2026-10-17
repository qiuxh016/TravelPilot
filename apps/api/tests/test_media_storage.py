from io import BytesIO
from uuid import uuid4

from travelpilot_api import media_storage
from travelpilot_api.settings import settings


def test_s3_media_round_trip_uses_owner_scoped_keys_and_private_server_side_encryption(monkeypatch):
    class S3Stub:
        def __init__(self):
            self.objects = {}
            self.put_calls = []
            self.delete_calls = []

        def put_object(self, **kwargs):
            self.put_calls.append(kwargs)
            self.objects[kwargs["Key"]] = kwargs["Body"]

        def get_object(self, *, Bucket, Key):
            return {"Body": BytesIO(self.objects[Key])}

        def delete_objects(self, **kwargs):
            self.delete_calls.append(kwargs)
            for item in kwargs["Delete"]["Objects"]:
                self.objects.pop(item["Key"], None)

    stub = S3Stub()
    monkeypatch.setattr(settings, "media_storage_backend", "s3")
    monkeypatch.setattr(settings, "media_s3_bucket", "private-bucket")
    monkeypatch.setattr(media_storage, "_s3_client", lambda: stub)
    owner_id, asset_id = uuid4(), uuid4()
    original, thumbnail = b"normalized-original", b"normalized-thumbnail"

    original_key, thumbnail_key = media_storage.write_image(
        owner_id, asset_id, "jpg", original, thumbnail,
    )

    assert original_key.startswith(f"users/{owner_id}/")
    assert thumbnail_key.startswith(f"users/{owner_id}/")
    assert [call["ServerSideEncryption"] for call in stub.put_calls] == ["AES256", "AES256"]
    assert all("ACL" not in call for call in stub.put_calls)
    assert media_storage.read_media(original_key) == original
    assert media_storage.read_media(thumbnail_key) == thumbnail

    media_storage.remove_media_keys([original_key, thumbnail_key])

    assert len(stub.delete_calls) == 1
    assert stub.objects == {}
