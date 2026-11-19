import ipaddress
import logging
import shutil
import socket
import tempfile
import time
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote

import httpx

from app.core.config import settings


logger = logging.getLogger(__name__)

MAX_REDIRECTS = 5
NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")

IMAGE_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"BM", "image/bmp", "bmp"),
    (b"II*\x00", "image/tiff", "tiff"),
    (b"MM\x00*", "image/tiff", "tiff"),
)


class ImageRejectedError(Exception):
    pass


class DownloadedImage(NamedTuple):
    path: Path
    media_type: str
    filename: str
    workdir: Path


def _is_public_address(address) -> bool:
    if address.version == 6:
        if address in NAT64_NETWORK or address.sixtofour or address.teredo:
            return False
        address = address.ipv4_mapped or address
    return address.is_global and not address.is_multicast


def _resolve_public_addresses(host: str, port: int) -> list[str]:
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise ImageRejectedError("The image host could not be resolved")

    addresses = {ipaddress.ip_address(result[4][0].split("%")[0]) for result in results}
    if not addresses or not all(_is_public_address(address) for address in addresses):
        raise ImageRejectedError("The image URL must point to a public address")
    return [str(address) for address in sorted(addresses, key=lambda item: item.version)]


def _save_response(response: httpx.Response, destination: Path, deadline: float) -> None:
    if response.status_code != 200:
        raise ImageRejectedError(f"The image URL returned status {response.status_code}")

    max_bytes = settings.IMAGE_DOWNLOADER_MAX_FILE_MB * 1024 * 1024
    too_large = ImageRejectedError(f"The image is larger than {settings.IMAGE_DOWNLOADER_MAX_FILE_MB} MB")
    declared_size = response.headers.get("content-length", "")
    if declared_size.isdigit() and int(declared_size) > max_bytes:
        raise too_large

    written = 0
    with destination.open("wb") as output:
        for chunk in response.iter_bytes():
            written += len(chunk)
            if written > max_bytes:
                raise too_large
            if time.monotonic() > deadline:
                raise ImageRejectedError("Downloading the image timed out")
            output.write(chunk)


def _fetch(client: httpx.Client, raw_url: str, destination: Path, deadline: float) -> str | None:
    try:
        url = httpx.URL(raw_url)
    except httpx.InvalidURL:
        raise ImageRejectedError("The image URL is invalid")
    if url.scheme not in ("http", "https") or not url.host or url.userinfo:
        raise ImageRejectedError("The image URL must be a plain http or https URL")

    default_port = 443 if url.scheme == "https" else 80
    addresses = _resolve_public_addresses(url.host, url.port or default_port)

    for address in addresses:
        try:
            with client.stream(
                "GET",
                url.copy_with(host=address),
                headers={"Host": url.netloc.decode("ascii")},
                extensions={"sni_hostname": url.raw_host.decode("ascii")},
            ) as response:
                if response.has_redirect_location:
                    return str(url.join(response.headers["location"]))
                _save_response(response, destination, deadline)
                return None
        except (httpx.ConnectError, httpx.ConnectTimeout):
            logger.warning("Could not connect to an image host address")
    raise ImageRejectedError("The image could not be downloaded")


def _download(url: str, destination: Path) -> None:
    deadline = time.monotonic() + settings.IMAGE_DOWNLOADER_DOWNLOAD_TIMEOUT_SECONDS
    try:
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            for _ in range(MAX_REDIRECTS + 1):
                url = _fetch(client, url, destination, deadline)
                if url is None:
                    return
    except httpx.HTTPError:
        raise ImageRejectedError("The image could not be downloaded")
    raise ImageRejectedError("The image URL redirected too many times")


def _detect_image_type(path: Path) -> tuple[str, str]:
    with path.open("rb") as source:
        header = source.read(16)
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp", "webp"
    if header[4:8] == b"ftyp" and header[8:12] in (b"avif", b"avis"):
        return "image/avif", "avif"
    for signature, media_type, extension in IMAGE_SIGNATURES:
        if header.startswith(signature):
            return media_type, extension
    raise ImageRejectedError("The file is not a supported image")


def _image_filename(url: str, extension: str) -> str:
    stem = Path(unquote(httpx.URL(url).path)).stem.strip()[:100]
    return f"{stem or 'image'}.{extension}"


def download_image(url: str) -> DownloadedImage:
    workdir = Path(tempfile.mkdtemp(prefix="image-downloader-"))
    try:
        source = workdir / "image"
        _download(url, source)
        media_type, extension = _detect_image_type(source)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    return DownloadedImage(source, media_type, _image_filename(url, extension), workdir)
