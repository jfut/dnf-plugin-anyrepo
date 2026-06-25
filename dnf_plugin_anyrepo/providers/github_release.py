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
EL_MARKER_RE = re.compile(
    r"(?P<marker>\.(?:module_)?el(?P<major>\d+)(?:[._][A-Za-z0-9]+)*)(?=\.[^.]+\.rpm$)"
)


class GitHubReleaseProvider:
    def __init__(self, config: RepoConfig):
        self.config = config
        self.release: Optional[Dict[str, object]] = None
        self.assets: List[Dict[str, object]] = []
        self.state: Dict[str, object] = load_state(config.cache_path)

    def refresh(self) -> bool:
        """Fetch metadata, apply age filtering, download RPMs, and update repodata."""

        release = self._fetch_latest_release()
        if not self._release_is_old_enough(release):
            if self.state and local_repo.has_repodata(self.config.cache_path):
                self.state["last_refresh_at"] = utcnow_iso()
                save_state(self.config.cache_path, self.state)
            return False

        assets = self._matching_assets(release)
        if not assets:
            raise ProviderError(f"{self.config.name}: no assets match {self.config.asset_regex}")

        self.release = release
        self.assets = assets
        cache_missing = not self._cached_assets_exist()
        changed = local_repo.desired_asset_changed(self.state, release, assets)
        if changed or cache_missing or not local_repo.has_repodata(self.config.cache_path):
            self._replace_cache()
            return True

        self.state["last_refresh_at"] = utcnow_iso()
        save_state(self.config.cache_path, self.state)
        return False

    def list_assets(self) -> List[Mapping[str, object]]:
        return list(self.assets)

    def download(self, cache_path: Optional[str] = None) -> None:
        if self.release is None:
            raise ProviderError("release metadata is not loaded")
        cache_path = cache_path or self.config.cache_path
        local_repo.ensure_cache_dirs(cache_path)
        local_repo.clean_packages(cache_path)
        for asset in self.assets:
            name = str(asset["name"])
            url = str(asset["browser_download_url"])
            destination = os.path.join(local_repo.packages_dir(cache_path), name)
            self._download_file(url, destination)

    def _replace_cache(self) -> None:
        parent = os.path.dirname(self.config.cache_path) or "."
        os.makedirs(parent, exist_ok=True)
        staging = tempfile.mkdtemp(prefix=f".{self.config.name}.", dir=parent)
        try:
            self.download(cache_path=staging)
            local_repo.run_createrepo(staging)
            self._save_state(cache_path=staging)
            local_repo.replace_cache(staging, self.config.cache_path)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _fetch_latest_release(self) -> Dict[str, object]:
        owner, repo = self.config.owner_repo
        latest_url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
        try:
            release = self._request_json(latest_url)
        except GitHubAPIError as exc:
            if not exc.transient:
                raise
            list_url = f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=5"
            releases = self._request_json(list_url)
            if not isinstance(releases, list):
                raise ProviderError(f"{self.config.name}: GitHub API returned invalid releases data")
            release = self._select_latest_published_release(releases)
            if release is None:
                raise ProviderError(f"{self.config.name}: no published GitHub releases found")
        if not isinstance(release, dict):
            raise ProviderError(f"{self.config.name}: GitHub API returned invalid release data")
        return release

    def _select_latest_published_release(
        self, releases: List[object]
    ) -> Optional[Dict[str, object]]:
        for release in releases:
            if not isinstance(release, dict):
                continue
            if release.get("draft") or release.get("prerelease"):
                continue
            return release
        return None

    def _matching_assets(self, release: Mapping[str, object]) -> List[Dict[str, object]]:
        pattern = re.compile(self.config.asset_regex)
        assets = []
        for asset in release.get("assets", []):
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name", ""))
            if pattern.search(name) and self._matches_arch(name):
                assets.append(asset)
        return self._filter_releasever_assets(assets)

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

    def _cached_assets_exist(self) -> bool:
        names = self.state.get("asset_names") or []
        if not names:
            return False
        return all(os.path.isfile(os.path.join(local_repo.packages_dir(self.config.cache_path), name)) for name in names)

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
            "arch": self.config.arch,
            "releasever": self.config.releasever,
            "updated_at": utcnow_iso(),
            "last_refresh_at": utcnow_iso(),
        }
        save_state(cache_path or self.config.cache_path, state)
        self.state = state

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
        import json

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
                    f"{self.config.name}: GitHub API returned HTTP {exc.code}",
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
