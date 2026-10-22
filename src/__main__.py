import asyncio
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from apify import Actor
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

try:
    import libtorrent as lt
except ImportError:
    lt = None

MAGICODE_UPLOAD_URL = "https://send.magicode.me"
MAX_FILE_SIZE = 10_000_000_000
UPLOAD_TIMEOUT_MS = 60 * 60 * 1000


async def upload_files(files: list[Path], root: Path) -> list[dict]:
    for file_path in files:
        if file_path.stat().st_size > MAX_FILE_SIZE:
            raise ValueError(
                f"{file_path.name} exceeds Magicode's 10 GB per-file limit"
            )

    uploaded = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            page.set_default_timeout(UPLOAD_TIMEOUT_MS)
            page.set_default_navigation_timeout(60_000)
            dialog_messages = []

            async def dismiss_dialog(dialog) -> None:
                dialog_messages.append(dialog.message)
                await dialog.dismiss()

            page.on("dialog", dismiss_dialog)
            for file_path in files:
                size = file_path.stat().st_size

                await page.goto(MAGICODE_UPLOAD_URL, wait_until="domcontentloaded")
                await page.locator("#drop-file-upload").wait_for(state="visible")
                await page.evaluate("""() => {
                    const input = document.createElement("input");
                    input.type = "file";
                    input.id = "magicode-file-source";
                    input.hidden = true;
                    document.body.appendChild(input);
                }""")
                await page.locator("#magicode-file-source").set_input_files(
                    str(file_path)
                )
                dialog_messages.clear()
                await page.evaluate("""() => {
                    const file = document.querySelector(
                        "#magicode-file-source"
                    ).files[0];
                    const dropZone = document.getElementById("drop-file-upload");
                    if (!file || !dropZone) {
                        throw new Error("Magicode upload controls were not found");
                    }
                    const dataTransfer = new DataTransfer();
                    dataTransfer.items.add(file);
                    dropZone.dispatchEvent(new DragEvent("dragover", {
                        dataTransfer,
                        bubbles: true,
                        cancelable: true
                    }));
                    dropZone.dispatchEvent(new DragEvent("drop", {
                        dataTransfer,
                        bubbles: true,
                        cancelable: true
                    }));
                }""")
                try:
                    await page.wait_for_function(
                        """() => {
                            const link = document.querySelector("#link-file");
                            return link && link.getAttribute("href");
                        }""",
                        timeout=UPLOAD_TIMEOUT_MS,
                    )
                except PlaywrightTimeoutError as error:
                    if dialog_messages:
                        raise RuntimeError(
                            "Magicode reported an upload error: "
                            + dialog_messages[-1]
                        ) from error
                    raise RuntimeError(
                        f"Timed out uploading {file_path.name} to Magicode"
                    ) from error
                download_url = await page.locator("#link-file").get_attribute("href")
                parsed_url = urlparse(download_url or "")
                if (
                    parsed_url.scheme != "https"
                    or parsed_url.netloc != "send.magicode.me"
                    or not parsed_url.path.startswith("/send-file/file/")
                ):
                    raise RuntimeError(
                        f"Magicode returned an unexpected download link for "
                        f"{file_path.name}"
                    )

                object_name = file_path.relative_to(root).as_posix()
                uploaded.append({
                    "name": object_name,
                    "size": size,
                    "url": download_url,
                })
                Actor.log.info("Uploaded %s to Magicode", object_name)
        finally:
            await browser.close()
    return uploaded


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        magnet_link = actor_input.get("magnetLink", "").strip()

        missing = [
            name for name, value in (("magnetLink", magnet_link),) if not value
        ]
        if missing:
            raise ValueError("Required input is missing: " + ", ".join(missing))
        if lt is None:
            raise RuntimeError("libtorrent is not installed")

        temp_dir = tempfile.mkdtemp(prefix="magnet-to-magicode-")
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
            Actor.log.info(
                "Downloaded %d file(s); uploading individually to Magicode",
                len(files),
            )
            uploaded = await upload_files(files, Path(temp_dir))

            output = {
                "downloaded": True,
                "uploaded": True,
                "destination": "send.magicode.me",
                "compressed": False,
                "totalFiles": len(files),
                "uploadedFiles": len(uploaded),
                "totalSize": sum(item["size"] for item in uploaded),
                "files": uploaded,
            }
            await Actor.push_data(output)
            Actor.log.info(
                "Completed: uploaded %d file(s) to Magicode", len(uploaded)
            )
        finally:
            shutil.rmtree(temp_dir)
            Actor.log.info("Temporary download directory removed")


if __name__ == "__main__":
    asyncio.run(main())
