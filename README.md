# Magnet to Magicode

Downloads files from a magnet or torrent link and uploads each file individually to [send.magicode.me](https://send.magicode.me). Files are not compressed, and the torrent's directory structure is retained in the output file names.

## Input

- `magnetLink` (required): magnet or torrent link.

The Magicode service currently allows files up to 10 GB and deletes them two days after upload. The Actor uploads files sequentially using a headless Chromium browser and returns the download link for each file.

## Output

The Actor reports the destination, file count, total size, and each uploaded file's name, size, and download URL in the Apify dataset. Keep the dataset private: the service says its links are not intended for public distribution.
