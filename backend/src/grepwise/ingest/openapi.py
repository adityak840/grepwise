"""Loading an OpenAPI 3.x spec from a URL or a local file (JSON or YAML)."""

import json
from pathlib import Path
from typing import Any

import httpx
import yaml

MAX_SPEC_BYTES = 20 * 1024 * 1024
TIMEOUT_SECONDS = 60.0


class OpenAPIError(Exception):
    pass


def _download(url: str) -> bytes:
    data = bytearray()
    try:
        with httpx.stream("GET", url, timeout=TIMEOUT_SECONDS, follow_redirects=True) as resp:
            resp.raise_for_status()
            for block in resp.iter_bytes():
                data.extend(block)
                if len(data) > MAX_SPEC_BYTES:
                    raise OpenAPIError(f"Spec is larger than {MAX_SPEC_BYTES // 2**20} MB.")
    except httpx.HTTPError as exc:
        raise OpenAPIError(f"Could not download {url}: {exc}") from exc
    return bytes(data)


def _read_file(location: str) -> bytes:
    path = Path(location)
    if not path.is_file():
        raise OpenAPIError(f"No such file: {location}")
    if path.stat().st_size > MAX_SPEC_BYTES:
        raise OpenAPIError(f"Spec is larger than {MAX_SPEC_BYTES // 2**20} MB.")
    return path.read_bytes()


def parse_spec(raw: bytes) -> dict[str, Any]:
    """Parse JSON or YAML and check it's OpenAPI 3.x."""
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise OpenAPIError(f"Not valid JSON or YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise OpenAPIError("Spec is not a JSON/YAML object.")
    version = str(data.get("openapi", ""))
    if not version.startswith("3."):
        found = f"swagger {data['swagger']}" if "swagger" in data else "no 'openapi' field"
        raise OpenAPIError(f"Only OpenAPI 3.x is supported (found {found}).")
    if not isinstance(data.get("paths"), dict):
        raise OpenAPIError("Spec has no 'paths'.")
    return data


def load_spec(location: str) -> dict[str, Any]:
    """`location` is an http(s) URL or a local file path."""
    is_url = location.startswith(("http://", "https://"))
    return parse_spec(_download(location) if is_url else _read_file(location))
