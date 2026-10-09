"""Confirmed public assets only; no execution, credentials, or automatic retry.

The caller supplies a dedicated task-local root and its persisted session ledger.
download_requests counts GETs (reserve 4, including redirects); the caller
allows at most 3 acquisition roots. DNS preflight is not IP pinning or
an OS sandbox: rebinding remains possible. Deadline checks bound processing and
HTTP timeouts, but cannot forcibly interrupt a blocking system DNS resolver.
"""
from __future__ import annotations

import ipaddress
import os
from io import BytesIO
import re
import socket
import ssl
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit

import httpx

from simple_ar.core.artifacts import read_json, write_json
from simple_ar.core.budget import BudgetLedger

LIMITS = {"caller_max_assets": 3, "max_gets": 4, "deadline_seconds": 120,
          "download_bytes": 20 * 1024 * 1024, "expanded_bytes": 80 * 1024 * 1024, "zip_items": 5000}


class _AssetError(ValueError):
    """Only adapter-owned, non-sensitive reason identifiers."""


def asset_limits(max_download_mb: int | None = None) -> dict[str, int]:
    """User-owned capacity shared by confirmation, reservation and extraction."""
    if max_download_mb is None:
        return dict(LIMITS)
    if type(max_download_mb) is not int or max_download_mb < 1:
        raise ValueError("Asset download capacity must be a positive integer in MiB")
    return {**LIMITS, "download_bytes": max_download_mb * 1024 * 1024,
            "expanded_bytes": max_download_mb * 4 * 1024 * 1024}


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("asset_deadline")
    return remaining


def _url(url: str, *, dns: bool = False) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.fragment or parsed.port not in (None, 443)
            or "\\" in url or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
        raise _AssetError("public_https_url_required")
    # Public document identities often live in the query (paper IDs, versions,
    # download selectors). Preserve them; a query is not itself authentication.
    # Credential-bearing/signed links remain unsuitable for public task records.
    for name, _ in parse_qsl(parsed.query, keep_blank_values=True):
        key = name.casefold().replace("-", "").replace("_", "")
        if key in {"key", "apikey", "token", "accesstoken", "auth", "authorization",
                   "password", "secret", "signature", "credential"} or key.startswith("xamz"):
            raise _AssetError("public_https_url_required")
    if dns:
        try:
            addresses = [ipaddress.ip_address(parsed.hostname)]
        except ValueError:
            addresses = [ipaddress.ip_address(row[4][0]) for row in
                         socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)]
        if not addresses or any(not address.is_global or address.is_multicast for address in addresses):
            raise _AssetError("nonpublic_address")
    return url


def validate_public_url(url: str, *, dns: bool = False) -> str:
    """Shared public HTTPS check; preflight is not DNS pinning or a sandbox."""
    return _url(url, dns=dns)


def public_document_response(url: str, *, max_bytes: int,
                             on_redirect: Callable[[], None] | None = None) -> BytesIO:
    """Bounded public GETs, no cookies, implicit proxies or retry.

    Redirects require the caller to account for each extra GET before sending it.
    Without that callback only one GET is allowed. Address/route checks apply
    to every hop; the same deadline and byte cap cover the whole acquisition.
    """
    if max_bytes <= 0:
        raise _AssetError("document_byte_budget_exhausted")
    deadline = time.monotonic() + LIMITS["deadline_seconds"]
    try:
        target = url
        for hop in range(LIMITS["max_gets"]):
            validate_public_url(target, dns=True)
            _remaining(deadline)
            if hop:
                try:
                    on_redirect()
                except RuntimeError:
                    raise _AssetError("document_request_budget_exhausted") from None
            with httpx.Client(trust_env=False, follow_redirects=False,
                              headers={"Accept-Encoding": "identity"},
                              **_document_transport(target)) as client:
                with client.stream("GET", target, timeout=min(30, _remaining(deadline))) as response:
                    if response.is_redirect:
                        if on_redirect is None:
                            raise _AssetError("document_redirect_requires_explicit_url")
                        if hop == LIMITS["max_gets"] - 1:
                            raise _AssetError("redirect_limit")
                        target = validate_public_url(urljoin(target, response.headers["location"]))
                        continue
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise _AssetError("encoded_download_rejected")
                    if int(response.headers.get("content-length", "0")) > max_bytes:
                        raise _AssetError("document_byte_limit")
                    body = bytearray()
                    for chunk in response.iter_raw():
                        _remaining(deadline)
                        if len(body) + len(chunk) > max_bytes:
                            raise _AssetError("document_byte_limit")
                        body.extend(chunk)
                    if not body:
                        raise _AssetError("empty_download")
                    result = BytesIO(body)
                    result.headers = response.headers.copy()
                    return result
    except _AssetError:
        raise
    except Exception as error:
        raise _AssetError(type(error).__name__) from None


def _document_transport(url: str) -> dict:
    """Explicit deployment route for exact source hosts, never model traffic."""
    reference = os.environ.get("SIMPLE_AR_DOCUMENT_PROXY_ENV", "")
    ca_bundle = os.environ.get("SIMPLE_AR_DOCUMENT_CA_BUNDLE", "")
    if not reference and not ca_bundle:
        return {}
    hosts = {host.strip().lower() for host in
             os.environ.get("SIMPLE_AR_DOCUMENT_ROUTE_HOSTS", "").split(",") if host.strip()}
    if not hosts or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) for host in hosts):
        raise _AssetError("document_route_requires_exact_hosts")
    if urlsplit(url).hostname.lower() not in hosts:
        return {}
    options = {}
    if reference:
        from simple_ar.integrations.model_profiles import resolve_proxy_env
        options["proxy"] = resolve_proxy_env(reference)
    if ca_bundle:
        options["verify"] = ssl.create_default_context(cafile=ca_bundle)
    return options


def asset_target(role: str, url: str) -> tuple[str, str]:
    parsed = urlsplit(_url(url))
    if role == "project" and parsed.hostname == "github.com" and len(parsed.path.strip("/").split("/")) == 2:
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", p) for p in parts):
            raise _AssetError("github_repository_url_required")
        owner, repo = parts
        repo = repo.removesuffix(".git")
        return f"https://api.github.com/repos/{owner}/{repo}/zipball", "default_branch_snapshot_not_fixed_commit"
    # URL suffixes neither prove nor disprove the format. Inspect the actual
    # bounded archive with zipfile before extraction; HTML/tar remain rejected.
    return url, "public_download"


def _unpack(archive: Path, destination: Path, deadline: float, *, max_expanded_bytes: int) -> None:
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > LIMITS["zip_items"] or sum(e.file_size for e in entries) > max_expanded_bytes:
            raise _AssetError("zip_limit")
        seen, planned = set(), []
        for entry in entries:
            name = entry.filename.rstrip("/")
            parts = name.split("/")
            mode = stat.S_IFMT(entry.external_attr >> 16)
            if (not name or "\\" in name or PurePosixPath(name).is_absolute()
                    or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or entry.orig_filename != entry.filename
                    or any(p in ("", ".", "..") or p.endswith((".", " ")) or ":" in p
                           or any(ord(c) < 32 for c in p)
                           or p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"{s}{n}" for s in ("COM", "LPT") for n in range(1, 10)]}
                           for p in parts) or name.casefold() in seen):
                raise _AssetError("unsafe_zip_entry")
            seen.add(name.casefold())
            planned.append((entry, destination.joinpath(*parts)))
        destination.mkdir()
        expanded = 0
        for entry, path in planned:
            _remaining(deadline)
            if entry.is_dir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, path.open("xb") as target:
                while chunk := source.read(64 * 1024):
                    _remaining(deadline)
                    expanded += len(chunk)
                    if expanded > max_expanded_bytes:
                        raise _AssetError("zip_expansion_limit")
                    target.write(chunk)


def acquire_asset(*, root: Path, role: str, url: str, ledger: BudgetLedger,
                  max_download_mb: int | None = None) -> dict:
    """Get one confirmed asset; data is never unpacked, project is never executed.

    Query-bearing URLs are deliberately excluded (including signed downloads).
    The root is single-request/single-writer; existing failures require a new,
    explicitly authorized root, not a retry through this API.
    """
    limits = asset_limits(max_download_mb)
    root = Path(root).absolute()
    receipt_path = root / "receipt.json"
    reservation = f"asset:{root}"
    receipt = {"path": str(root), "status": "started", "url": url, "role": role,
               "downloaded_bytes": 0, "get_requests": 0, "limits": limits}
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (root, *root.parents)):
        return {**receipt, "status": "failed", "reason": "UnsafeAssetRoot"}  # Never write through a symlink.
    deadline, reserved = time.monotonic() + LIMITS["deadline_seconds"], False
    try:
        if receipt_path.exists():
            saved = read_json(receipt_path)
            if not isinstance(saved, dict):
                raise _AssetError("invalid_asset_receipt")
            receipt = saved
            if receipt.get("url") != url or receipt.get("role") != role:
                return {**receipt, "status": "failed", "reason": "AssetRootRequestConflict"}
            if receipt["status"] == "completed":
                path = Path(receipt["path"])
                if (not path.is_absolute() or not path.resolve().is_relative_to(root.resolve())
                        or not (path.is_dir() if role == "project" else role == "data" and path.is_file())):
                    raise _AssetError("completed_asset_path_missing_or_wrong_role")
            if receipt["status"] == "started":
                entry = next((e for e in ledger.entries if e.reservation_id == reservation), None)
                if entry is not None and entry.status == "reserved":
                    ledger.mark_unknown(reservation, reason="InterruptedAsset", retain_reservation=True)
                receipt.update(status="unknown", reason="InterruptedAsset")
                write_json(receipt_path, receipt)
            return receipt
        occupied = root.exists() and any(root.iterdir())
        root.mkdir(parents=True, exist_ok=True)
        write_json(receipt_path, receipt)
        if occupied or role not in {"project", "data"} or ledger.storage_path is None:
            raise _AssetError("dedicated_root_role_and_persisted_ledger_required")
        target, provenance = asset_target(role, url)
        suffix = Path(unquote(urlsplit(url).path).rsplit("/", 1)[-1]).suffix
        suffix = suffix.lower() if re.fullmatch(r"\.[a-zA-Z0-9]{1,12}", suffix) else ".bin"
        download = root / ("download.zip" if role == "project" else "download" + suffix)
        receipt.update(path=str(download), provenance=provenance)
        write_json(receipt_path, receipt)
        entry = ledger.reserve(reservation, {"download_requests": 4,
                                            "download_bytes": limits["download_bytes"]}, purpose="confirmed_public_asset")
        if entry.status != "reserved":
            raise _AssetError("asset_reservation_already_used")
        reserved = True
        with httpx.Client(trust_env=False, follow_redirects=False, headers={"Accept-Encoding": "identity"}) as client:
            for hop in range(4):
                _url(target, dns=True)
                client.cookies.clear()
                receipt["get_requests"] += 1
                write_json(receipt_path, receipt)
                with client.stream("GET", target, timeout=min(30, _remaining(deadline))) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        if hop == 3:
                            raise _AssetError("redirect_limit")
                        target = _url(urljoin(target, response.headers["location"]))
                        continue
                    response.raise_for_status()
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise _AssetError("encoded_download_rejected")
                    if int(response.headers.get("content-length", "0")) > limits["download_bytes"]:
                        raise _AssetError("download_limit")
                    with download.open("xb") as output:
                        for chunk in response.iter_raw():  # Do not aggregate slow streams before deadline checks.
                            _remaining(deadline)
                            receipt["downloaded_bytes"] += len(chunk)
                            if receipt["downloaded_bytes"] > limits["download_bytes"]:
                                raise _AssetError("download_limit")
                            output.write(chunk)
                    break
        if receipt["downloaded_bytes"] == 0:
            raise _AssetError("empty_download")
        if role == "project":
            _unpack(download, root / "project", deadline, max_expanded_bytes=limits["expanded_bytes"])
            children = list((root / "project").iterdir())
            receipt["path"] = str(children[0] if len(children) == 1 and children[0].is_dir() else root / "project")
        _remaining(deadline)
        ledger.settle(reservation, {"download_requests": receipt["get_requests"],
                                   "download_bytes": receipt["downloaded_bytes"]}, actual_source="asset_stream")
        receipt["status"] = "completed"
    except BaseException as error:
        receipt.update(status="unknown" if not isinstance(error, Exception) else "failed",
                       reason=str(error) if isinstance(error, _AssetError) else type(error).__name__)
        if reserved and entry.status == "reserved":
            try:
                ledger.mark_unknown(reservation, reason=type(error).__name__, retain_reservation=True)
            except Exception as budget_error:
                receipt.update(status="unknown", reason=type(budget_error).__name__)
    try:
        write_json(receipt_path, receipt)
    except Exception as storage_error:
        receipt.update(status="unknown", reason=type(storage_error).__name__)
    return receipt
