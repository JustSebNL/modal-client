import dataclasses
import os
import ssl
from typing import Optional

import certifi
from aiohttp import ClientSession, TCPConnector

from modal_proto import api_pb2
from modal_utils.async_utils import retry
from modal_utils.blob_utils import use_md5
from modal_utils.hash_utils import get_md5_base64, get_sha256_hex

# Max size for function inputs and outputs.
MAX_OBJECT_SIZE_BYTES = 64 * 1024  # 64 kb

#  If a file is LARGE_FILE_LIMIT bytes or larger, it's uploaded to blob store (s3) instead of going through grpc
#  It will also make sure to chunk the hash calculation to avoid reading the entire file into memory
LARGE_FILE_LIMIT = 1024 * 1024  # 1MB


def http_client_with_tls() -> ClientSession:
    """Create a new HTTP client session with standard, bundled TLS certificates.

    This is necessary to prevent client issues on some system where Python does
    not come pre-installed with specific TLS certificates that are necessary to
    connect to AWS S3 bucket URLs.

    Specifically: the error "unable to get local issuer certificate" when making
    an aiohttp request.
    """
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = TCPConnector(ssl=ssl_context)
    return ClientSession(connector=connector)


@retry(n_attempts=5, base_delay=0.1, timeout=None)
async def _upload_to_url(upload_url: str, content_md5: str, aiohttp_payload) -> None:
    async with http_client_with_tls() as session:
        headers = {"content-type": "application/octet-stream"}

        if use_md5(upload_url):
            headers["Content-MD5"] = content_md5

        async with session.put(upload_url, data=aiohttp_payload, headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Put to {upload_url} failed with status {resp.status}: {text}")


async def _blob_upload(content_md5: str, aiohttp_payload, stub) -> str:
    req = api_pb2.BlobCreateRequest(content_md5=content_md5)
    resp = await stub.BlobCreate(req)

    blob_id = resp.blob_id
    target = resp.upload_url

    await _upload_to_url(target, content_md5, aiohttp_payload)

    return blob_id


async def blob_upload(payload: bytes, stub) -> str:
    content_md5 = get_md5_base64(payload)
    return await _blob_upload(content_md5, payload, stub)


async def blob_upload_file(filename: str, stub) -> str:
    content_md5 = get_md5_base64(open(filename, "rb"))
    with open(filename, "rb") as fp:
        return await _blob_upload(content_md5, fp, stub)


@retry(n_attempts=5, base_delay=0.1, timeout=None)
async def _download_from_url(download_url):
    async with http_client_with_tls() as session:
        async with session.get(download_url) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Get from {download_url} failed with status {resp.status}: {text}")
            return await resp.read()


async def blob_download(blob_id, stub):
    # convenience function reading all of the downloaded file into memory
    req = api_pb2.BlobGetRequest(blob_id=blob_id)
    resp = await stub.BlobGet(req)

    return await _download_from_url(resp.download_url)


@dataclasses.dataclass
class FileUploadSpec:
    filename: str
    rel_filename: str

    use_blob: bool
    content: Optional[bytes]  # typically None if using blob, required otherwise
    sha256_hex: str
    size: int


def get_file_upload_spec(filename, rel_filename):
    # Somewhat CPU intensive, so we run it in a thread/process
    size = os.path.getsize(filename)
    if size >= LARGE_FILE_LIMIT:
        use_blob = True
        content = None
        with open(filename, "rb") as fp:
            sha256_hex = get_sha256_hex(fp)
    else:
        use_blob = False
        with open(filename, "rb") as fp:
            content = fp.read()
        sha256_hex = get_sha256_hex(content)
    return FileUploadSpec(filename, rel_filename, use_blob=use_blob, content=content, sha256_hex=sha256_hex, size=size)
