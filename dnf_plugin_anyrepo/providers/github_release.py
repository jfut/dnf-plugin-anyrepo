# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

"""GitHub Releases provider for RPM assets."""

import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Mapping, Optional

from dnf_plugin_anyrepo import repo as local_repo
from dnf_plugin_anyrepo.config import RepoConfig
from dnf_plugin_anyrepo.state import load_state, save_state, utcnow_iso


class ProviderError(RuntimeError):
    """Raised when GitHub releases cannot be read or cached."""


class GitHubAPIError(ProviderError):
    """Raised for GitHub API failures so transient errors can be retried."""

    def __init__(self, message: str, status_code: Optional[int] = None, transient: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.transient = transient


TRANSIENT_HTTP_STATUS = {502, 503, 504}
GITHUB_RELEASES_PER_PAGE = 100
EL_MARKER_RE = re.compile(
    r"(?P<marker>\.(?:module_)?el(?P<major>\d+)(?:[._][A-Za-z0-9]+)*)(?=\.[^.]+\.rpm$)"
)


class GitHubReleaseProvider:
    def __init__(self, config: RepoConfig):
        self.config = config
        self.release: Optional[Dict[str, object]] = None
        self.assets: List[Dict[str, object]] = []
        self.debug_assets: List[Dict[str, object]] = []
        self.source_assets: List[Dict[str, object]] = []
        self.state: Dict[str, object] = load_state(config.cache_path)

    def refresh(self) -> bool:
        """Fetch metadata, apply age filtering, download RPMs, and update repodata."""

        release = self._fetch_latest_eligible_release()
        if release is None:
            # Keep a usable older cache until a newer release satisfies minimum_release_age.
            if local_repo.has_repodata(self.config.cache_path):
                self.state["last_refresh_at"] = utcnow_iso()
                save_state(self.config.cache_path, self.state)
            return False

        assets = self._matching_assets(release)
        if not assets:
            raise ProviderError(f"{self.config.name}: no assets match {self.config.asset_include}")
        debug_assets = self._matching_debug_assets(release)
        source_assets = self._matching_source_assets(release)

        self.release = release
        self.assets = assets
        self.debug_assets = debug_assets
        self.source_assets = source_assets
        cache_missing = not self._asset_cache_complete(
            assets,
            self.config.cache_path,
            "asset_names",
        )
        cache_missing = cache_missing or not self._asset_cache_complete(
            debug_assets,
            self._debug_cache_path(),
            "debug_asset_names",
        )
        cache_missing = cache_missing or not self._asset_cache_complete(
            source_assets,
            self._source_cache_path(),
            "source_asset_names",
        )
        changed = local_repo.desired_asset_changed(self.state, release, assets)
        changed = changed or self._asset_group_changed(debug_assets, prefix="debug_")
        changed = changed or self._asset_group_changed(source_assets, prefix="source_")
        if changed or cache_missing:
            self._replace_cache()
            return True

        self.state["last_refresh_at"] = utcnow_iso()
        save_state(self.config.cache_path, self.state)
        return False

    def validate(self) -> None:
        """Ensure recent releases include an AnyRepo-compatible RPM asset."""

        owner, repo = self.config.owner_repo
        list_url = (
            f"https://api.github.com/repos/{owner}/{repo}/releases"
            f"?per_page={GITHUB_RELEASES_PER_PAGE}&page=1"
        )
        releases = self._request_json(list_url)
        if not isinstance(releases, list):
            raise ProviderError(f"{self.config.name}: GitHub API returned invalid releases data")
        for release in releases:
            if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
                continue
            if self._matching_assets(release):
                return
        raise ProviderError(
            f"{self.config.name}: no compatible RPM assets found in the latest "
            f"{GITHUB_RELEASES_PER_PAGE} GitHub Releases"
        )

    def list_assets(self) -> List[Mapping[str, object]]:
        return list(self.assets)

    def download(self, cache_path: Optional[str] = None) -> None:
        if self.release is None:
            raise ProviderError("release metadata is not loaded")
        self._download_asset_group(self.assets, cache_path or self.config.cache_path)

    def _replace_cache(self) -> None:
        parent = os.path.dirname(self.config.cache_path) or "."
        os.makedirs(parent, exist_ok=True)
        staging = tempfile.mkdtemp(prefix=f".{self.config.name}.", dir=parent)
        try:
            self.download(cache_path=staging)
            local_repo.run_createrepo(staging)
            if self.debug_assets:
                debug_cache_path = self._debug_cache_path(staging)
                self._download_asset_group(self.debug_assets, debug_cache_path)
                local_repo.run_createrepo(debug_cache_path)
            if self.source_assets:
                source_cache_path = self._source_cache_path(staging)
                self._download_asset_group(self.source_assets, source_cache_path)
                local_repo.run_createrepo(source_cache_path)
            self._save_state(cache_path=staging)
            local_repo.replace_cache(staging, self.config.cache_path)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _fetch_latest_eligible_release(self) -> Optional[Dict[str, object]]:
        owner, repo = self.config.owner_repo
        page = 1
        while True:
            list_url = (
                f"https://api.github.com/repos/{owner}/{repo}/releases"
                f"?per_page={GITHUB_RELEASES_PER_PAGE}&page={page}"
            )
            releases = self._request_json(list_url)
            if not isinstance(releases, list):
                raise ProviderError(f"{self.config.name}: GitHub API returned invalid releases data")
            selected = self._select_latest_eligible_release(releases)
            if selected is not None:
                return selected
            if len(releases) < GITHUB_RELEASES_PER_PAGE:
                return None
            page += 1

    def _select_latest_eligible_release(
        self, releases: List[object]
    ) -> Optional[Dict[str, object]]:
        for release in releases:
            if not isinstance(release, dict):
                continue
            if release.get("draft") or release.get("prerelease"):
                continue
            if self._release_is_old_enough(release):
                return release
        return None

    def _matching_assets(self, release: Mapping[str, object]) -> List[Dict[str, object]]:
        return self._select_assets(
            release,
            include_pattern=self.config.asset_include,
            exclude_pattern=self.config.asset_exclude,
            match_arch=True,
        )

    def _matching_debug_assets(self, release: Mapping[str, object]) -> List[Dict[str, object]]:
        return self._select_assets(
            release,
            include_pattern=r"(?:-debuginfo(?:-|[.])|-debugsource(?:-|[.])).*[.]rpm$",
            match_arch=True,
        )

    def _matching_source_assets(self, release: Mapping[str, object]) -> List[Dict[str, object]]:
        return self._select_assets(
            release,
            include_pattern=r"[.]src[.]rpm$",
            match_arch=False,
        )

    def _select_assets(
        self,
        release: Mapping[str, object],
        include_pattern: str,
        exclude_pattern: Optional[str] = None,
        match_arch: bool = True,
    ) -> List[Dict[str, object]]:
        pattern = re.compile(include_pattern)
        exclude = re.compile(exclude_pattern) if exclude_pattern else None
        assets = []
        for asset in release.get("assets", []):
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name", ""))
            if exclude and exclude.search(name):
                continue
            if not pattern.search(name):
                continue
            if match_arch and not self._matches_arch(name):
                continue
            self._validate_asset_name(name)
            assets.append(asset)
        return self._filter_releasever_assets(assets)

    def _validate_asset_name(self, name: str) -> None:
        """Reject release asset names that could escape the package directory."""

        if not name or name in {".", ".."} or any(char in name for char in "\x00/\\"):
            raise ProviderError(f"{self.config.name}: invalid GitHub asset name: {name!r}")

    def _matches_arch(self, name: str) -> bool:
        """Apply the RPM arch filter while allowing noarch packages."""

        if self.config.arch:
            arch_suffix = f".{self.config.arch}.rpm"
            if not (name.endswith(arch_suffix) or name.endswith(".noarch.rpm")):
                return False
        return True

    def _filter_releasever_assets(self, assets: List[Dict[str, object]]) -> List[Dict[str, object]]:
        if not self.config.releasever:
            return assets

        grouped: Dict[str, List[Dict[str, object]]] = {}
        for asset in assets:
            name = str(asset.get("name", ""))
            grouped.setdefault(_releasever_group_key(name), []).append(asset)

        selected = []
        for group in grouped.values():
            has_el_variants = any(_releasever_from_name(str(asset.get("name", ""))) for asset in group)
            fallback_releasever = _closest_compatible_releasever(
                self.config.releasever,
                [_releasever_from_name(str(asset.get("name", ""))) for asset in group],
            )
            for asset in group:
                name = str(asset.get("name", ""))
                releasever = _releasever_from_name(name)
                # EL variants fall back to the nearest lower major when an exact match is unavailable.
                if not has_el_variants or releasever == fallback_releasever:
                    selected.append(asset)
        return selected

    def _release_is_old_enough(self, release: Mapping[str, object]) -> bool:
        published_at = str(release.get("published_at", ""))
        try:
            published = _parse_github_time(published_at)
        except ValueError as exc:
            raise ProviderError(f"{self.config.name}: invalid published_at: {published_at}") from exc
        age = (datetime.now(timezone.utc) - published).total_seconds()
        return age >= self.config.minimum_release_age

    def _asset_cache_complete(
        self,
        assets: List[Dict[str, object]],
        cache_path: str,
        state_key: str,
    ) -> bool:
        if not assets:
            return True
        names = self.state.get(state_key) or []
        if not names:
            return False
        if not local_repo.has_repodata(cache_path):
            return False
        return all(os.path.isfile(os.path.join(local_repo.packages_dir(cache_path), name)) for name in names)

    def _asset_group_changed(self, assets: List[Dict[str, object]], prefix: str) -> bool:
        return (
            self.state.get(f"{prefix}asset_ids") != [asset.get("id") for asset in assets]
            or self.state.get(f"{prefix}asset_updated_at")
            != [asset.get("updated_at") for asset in assets]
            or self.state.get(f"{prefix}asset_names") != [asset.get("name") for asset in assets]
        )

    def _save_state(self, cache_path: Optional[str] = None) -> None:
        if self.release is None:
            return
        state = {
            "url": self.config.url,
            "latest_release_id": self.release.get("id"),
            "latest_tag": self.release.get("tag_name"),
            "asset_ids": [asset.get("id") for asset in self.assets],
            "asset_names": [asset.get("name") for asset in self.assets],
            "asset_updated_at": [asset.get("updated_at") for asset in self.assets],
            "debug_asset_ids": [asset.get("id") for asset in self.debug_assets],
            "debug_asset_names": [asset.get("name") for asset in self.debug_assets],
            "debug_asset_updated_at": [asset.get("updated_at") for asset in self.debug_assets],
            "source_asset_ids": [asset.get("id") for asset in self.source_assets],
            "source_asset_names": [asset.get("name") for asset in self.source_assets],
            "source_asset_updated_at": [asset.get("updated_at") for asset in self.source_assets],
            "arch": self.config.arch,
            "releasever": self.config.releasever,
            "updated_at": utcnow_iso(),
            "last_refresh_at": utcnow_iso(),
        }
        save_state(cache_path or self.config.cache_path, state)
        self.state = state

    def _download_asset_group(
        self,
        assets: List[Dict[str, object]],
        cache_path: str,
    ) -> None:
        local_repo.ensure_cache_dirs(cache_path)
        local_repo.clean_packages(cache_path)
        for asset in assets:
            name = str(asset["name"])
            url = str(asset["browser_download_url"])
            destination = os.path.join(local_repo.packages_dir(cache_path), name)
            self._download_file(url, destination)

    def _debug_cache_path(self, cache_path: Optional[str] = None) -> str:
        return local_repo.subrepo_cache_path(cache_path or self.config.cache_path, "debuginfo")

    def _source_cache_path(self, cache_path: Optional[str] = None) -> str:
        return local_repo.subrepo_cache_path(cache_path or self.config.cache_path, "source")

    def _download_file(self, url: str, destination: str) -> None:
        request = urllib.request.Request(url, headers=self._headers())
        tmp = f"{destination}.tmp"
        try:
            with urllib.request.urlopen(request, timeout=60) as response, open(tmp, "wb") as fh:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
        except urllib.error.URLError as exc:
            raise ProviderError(f"{self.config.name}: failed to download {url}: {exc}") from exc
        os.replace(tmp, destination)

    def _request_json(self, url: str) -> Any:
        delay = 1.0
        for attempt in range(3):
            request = urllib.request.Request(url, headers=self._headers())
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in TRANSIENT_HTTP_STATUS and attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise GitHubAPIError(
                    self._format_http_error_message(exc),
                    status_code=exc.code,
                    transient=exc.code in TRANSIENT_HTTP_STATUS,
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < 2:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise GitHubAPIError(
                    f"{self.config.name}: GitHub API request failed: {exc}",
                    transient=True,
                ) from exc

    def _format_http_error_message(self, exc: urllib.error.HTTPError) -> str:
        # Include GitHub's response body so API failures such as rate limits are actionable.
        detail = self._read_http_error_detail(exc)
        if exc.code == 403 and "API rate limit" in detail:
            rate_limit_reset = self._read_rate_limit_reset_time()
            if rate_limit_reset:
                detail = f"{detail}\n{rate_limit_reset}"
        message = f"{self.config.name}: GitHub API returned HTTP {exc.code}"
        if detail:
            return f"{message}: {detail}"
        return message

    def _read_rate_limit_reset_time(self) -> str:
        request = urllib.request.Request(
            "https://api.github.com/rate_limit",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, UnicodeDecodeError):
            return ""

        if not isinstance(payload, dict):
            return ""
        rate = payload.get("rate")
        if not isinstance(rate, dict):
            return ""
        reset = rate.get("reset")
        if not isinstance(reset, int):
            return ""

        # Convert the Unix timestamp into the system local timezone for the error message.
        reset_at = (
            datetime.fromtimestamp(reset, tz=timezone.utc)
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S %Z")
        )
        return f"The rate limit will reset at {reset_at}."

    def _read_http_error_detail(self, exc: urllib.error.HTTPError) -> str:
        if exc.fp is None:
            return ""
        try:
            body = exc.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return ""
        if not body:
            return ""
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return " ".join(body.split())
        if isinstance(payload, dict):
            message = str(payload.get("message", "")).strip()
            if message:
                return message
        return " ".join(body.split())

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "dnf-plugin-anyrepo",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self._read_token()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _read_token(self) -> Optional[str]:
        path = self.config.github_token_file
        if not path:
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()


def _parse_github_time(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        if value.endswith("+00:00"):
            parsed = datetime.strptime(value[:-6], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _releasever_from_name(name: str) -> Optional[str]:
    match = EL_MARKER_RE.search(name)
    if not match:
        return None
    return f"el{match.group('major')}"


def _releasever_group_key(name: str) -> str:
    return EL_MARKER_RE.sub("", name)


def _closest_compatible_releasever(
    desired_releasever: str, available_releasevers: List[Optional[str]]
) -> Optional[str]:
    desired_major = _releasever_major(desired_releasever)
    if desired_major is None:
        return desired_releasever

    candidates = []
    for releasever in available_releasevers:
        major = _releasever_major(releasever)
        if major is not None and major <= desired_major:
            candidates.append(major)
    if not candidates:
        return None
    return f"el{max(candidates)}"


def _releasever_major(releasever: Optional[str]) -> Optional[int]:
    if not releasever:
        return None
    match = re.fullmatch(r"el(\d+)", releasever)
    if not match:
        return None
    return int(match.group(1))
