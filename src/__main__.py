import asyncio
import mimetypes
import shutil
import tempfile
import time
from pathlib import Path

from apify import Actor

try:
    import libtorrent as lt
except ImportError:
    lt = None

try:
    from b2sdk.v2 import B2Api, InMemoryAccountInfo
except ImportError:
    B2Api = None
    InMemoryAccountInfo = None


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        magnet_link = actor_input.get("magnetLink", "").strip()
        application_key_id = actor_input.get("b2ApplicationKeyId", "").strip()
        application_key = actor_input.get("b2ApplicationKey", "")
        bucket_name = actor_input.get("b2BucketName", "").strip()

        missing = [
            name for name, value in (
                ("magnetLink", magnet_link),
                ("b2ApplicationKeyId", application_key_id),
                ("b2ApplicationKey", application_key),
                ("b2BucketName", bucket_name),
            ) if not value
        ]
        if missing:
            raise ValueError("Required input is missing: " + ", ".join(missing))
        if lt is None:
            raise RuntimeError("libtorrent is not installed")
        if B2Api is None or InMemoryAccountInfo is None:
            raise RuntimeError("b2sdk is not installed")

        temp_dir = tempfile.mkdtemp(prefix="magnet-to-b2-")
        Actor.log.info("Starting magnet download")
        try:
            session = lt.session()
            handle = lt.add_magnet_uri(session, magnet_link, {
                "save_path": temp_dir,
                "storage_mode": lt.storage_mode_t(2),
            })

            Actor.log.info("Waiting for torrent metadata")
            while not handle.has_metadata():
                await asyncio.sleep(1)

            torrent_info = handle.get_torrent_info()
            Actor.log.info(
                "Metadata received; download size %.2f MB",
                torrent_info.total_size() / (1024 * 1024),
            )
            handle.set_sequential_download(True)
            last_log = 0.0
            while not handle.is_seed():
                status = handle.status()
                now = time.monotonic()
                if now - last_log >= 10:
                    Actor.log.info(
                        "Download progress: %.1f%% at %.1f KB/s",
                        status.progress * 100,
                        status.download_rate / 1024,
                    )
                    last_log = now
                await asyncio.sleep(1)
            session.pause()

            files = sorted(
                path for path in Path(temp_dir).rglob("*")
                if path.is_file() and not path.name.startswith(".")
            )
            if not files:
                raise RuntimeError("Torrent completed but no files were found")
            Actor.log.info("Downloaded %d file(s); uploading directly to B2 without ZIP", len(files))

            info = InMemoryAccountInfo()
            api = B2Api(info)
            await asyncio.to_thread(
                api.authorize_account,
                "production",
                application_key_id,
                application_key,
            )
            bucket = await asyncio.to_thread(api.get_bucket_by_name, bucket_name)

            uploaded = []
            for file_path in files:
                object_name = file_path.relative_to(temp_dir).as_posix()
                content_type = mimetypes.guess_type(file_path.name)[0] or "b2/x-auto"
                uploaded_file = await asyncio.to_thread(
                    bucket.upload_local_file,
                    local_file=str(file_path),
                    file_name=object_name,
                    content_type=content_type,
                )
                uploaded.append({
                    "name": object_name,
                    "size": file_path.stat().st_size,
                    "fileId": uploaded_file.id_,
                })
                Actor.log.info("Uploaded %s", object_name)

            output = {
                "downloaded": True,
                "uploaded": True,
                "bucketName": bucket_name,
                "compressed": False,
                "totalFiles": len(files),
                "uploadedFiles": len(uploaded),
                "totalSize": sum(item["size"] for item in uploaded),
                "files": uploaded,
            }
            await Actor.push_data(output)
            Actor.log.info("Completed: uploaded %d file(s) to B2", len(uploaded))
        finally:
            shutil.rmtree(temp_dir)
            Actor.log.info("Temporary download directory removed")


if __name__ == "__main__":
    asyncio.run(main())
