"""Private local or S3-compatible media storage; keys are server-generated."""
import logging
from io import BytesIO
from pathlib import Path, PurePosixPath
from uuid import UUID

from PIL import Image, ImageOps

from travelpilot_api.settings import settings

ALLOWED_FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
MIME_BY_EXTENSION = {extension: mime for extension, mime in ALLOWED_FORMATS.values()}
logger = logging.getLogger("travelpilot.media")


def _s3_client():
    import boto3
    from botocore.config import Config

    endpoint = settings.media_s3_endpoint_url or None
    return boto3.client(
        "s3",
        region_name=settings.media_s3_region,
        endpoint_url=endpoint,
        aws_access_key_id=settings.media_s3_access_key_id or None,
        aws_secret_access_key=settings.media_s3_secret_access_key or None,
        config=Config(s3={"addressing_style": "path"}) if endpoint else None,
    )


def normalize_image(data: bytes, content_type: str) -> tuple[bytes, bytes, str, str]:
    """Decode and re-encode one still image, dropping metadata including EXIF/GPS."""
    try:
        with Image.open(BytesIO(data)) as probe:
            image_format = probe.format or ""
            if image_format not in ALLOWED_FORMATS or getattr(probe, "n_frames", 1) != 1:
                raise ValueError("仅支持单帧 JPEG、PNG 或 WebP 图片")
            probe.verify()
        expected_type = ALLOWED_FORMATS[image_format][1]
        if content_type.split(";", 1)[0].strip().lower() != expected_type:
            raise ValueError("图片格式与 Content-Type 不匹配")
        with Image.open(BytesIO(data)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("图片分辨率过大")
            image = ImageOps.exif_transpose(source)
            image.load()
            image.thumbnail((4096, 4096))
            image.info.clear()
            if image_format == "JPEG" and image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            clean = BytesIO()
            options = {"format": image_format}
            if image_format == "JPEG":
                options.update(quality=90, optimize=True)
            elif image_format == "PNG":
                options.update(optimize=True)
            else:
                options.update(quality=90, method=4)
            image.save(clean, **options)
            thumb_image = image.copy()
            thumb_image.thumbnail((720, 720))
            thumb = BytesIO()
            thumb_image.save(thumb, **options)
        extension, mime_type = ALLOWED_FORMATS[image_format]
        return clean.getvalue(), thumb.getvalue(), extension, mime_type
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("无法读取该图片，或图片分辨率不安全") from exc


def write_image(owner_id: UUID, asset_id: UUID, extension: str, original: bytes, thumbnail: bytes) -> tuple[str, str]:
    folder = PurePosixPath("users") / str(owner_id)
    original_key = str(folder / f"{asset_id}.{extension}")
    thumbnail_key = str(folder / f"{asset_id}_thumb.{extension}")
    if settings.media_storage_backend.lower() == "s3":
        client = _s3_client()
        uploaded: list[str] = []
        try:
            for key, content in ((original_key, original), (thumbnail_key, thumbnail)):
                client.put_object(
                    Bucket=settings.media_s3_bucket,
                    Key=key,
                    Body=content,
                    ContentType=MIME_BY_EXTENSION[extension],
                    ServerSideEncryption="AES256",
                )
                uploaded.append(key)
        except Exception:
            remove_media_keys(uploaded)
            raise
        return original_key, thumbnail_key

    root = Path(settings.media_storage_dir).resolve()
    written: list[str] = []
    try:
        for key, content in ((original_key, original), (thumbnail_key, thumbnail)):
            target = (root / key).resolve()
            if root not in target.parents:
                raise ValueError("Invalid media path")
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(target.suffix + ".tmp")
            temp.write_bytes(content)
            temp.replace(target)
            written.append(key)
    except Exception:
        remove_media_keys(written)
        raise
    return original_key, thumbnail_key


def media_path(key: str) -> Path:
    if settings.media_storage_backend.lower() == "s3":
        raise RuntimeError("S3 media does not have a local filesystem path")
    root = Path(settings.media_storage_dir).resolve()
    path = (root / key).resolve()
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError(key)
    return path


def read_media(key: str) -> bytes:
    if settings.media_storage_backend.lower() == "s3":
        response = _s3_client().get_object(Bucket=settings.media_s3_bucket, Key=key)
        return response["Body"].read()
    return media_path(key).read_bytes()


def remove_media_keys(keys: list[str]) -> None:
    if settings.media_storage_backend.lower() == "s3":
        if not keys:
            return
        try:
            client = _s3_client()
            for offset in range(0, len(keys), 1000):
                client.delete_objects(
                    Bucket=settings.media_s3_bucket,
                    Delete={"Objects": [{"Key": key} for key in keys[offset:offset + 1000]], "Quiet": True},
                )
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the primary operation
            logger.warning("media_cleanup_failed error_type=%s", type(exc).__name__)
        return
    root = Path(settings.media_storage_dir).resolve()
    for key in keys:
        path = (root / key).resolve()
        if root in path.parents:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("media_cleanup_failed error_type=%s", type(exc).__name__)
