# Magnet to Backblaze B2

Downloads files from a magnet or torrent link and uploads each file directly to a Backblaze B2 bucket. Files are not compressed.

## Input

- `magnetLink` (required): magnet or torrent link.
- `b2ApplicationKeyId` (required): Backblaze B2 application key ID.
- `b2ApplicationKey` (required): Backblaze B2 application key. The input is marked secret.
- `b2BucketName` (required): destination bucket name.

Create an application key in Backblaze with access limited to the intended bucket and the minimum required write permissions. Credentials are supplied as run input and are not logged by the Actor.

## Output

The Actor reports the destination bucket, file count, total size, and uploaded object names and IDs in the Apify dataset. Torrent directory structure is preserved in B2 object names.
