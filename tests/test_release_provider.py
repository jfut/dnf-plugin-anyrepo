# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the dnf-plugin-anyrepo project.

import os
import tempfile
import unittest
import urllib.error
import io
from datetime import datetime, timedelta, timezone
from unittest import mock

from dnf_plugin_anyrepo.config import RepoConfig
from dnf_plugin_anyrepo.providers.github_release import ProviderError, GitHubReleaseProvider
from dnf_plugin_anyrepo.state import save_state


class GitHubReleaseProviderTest(unittest.TestCase):
    def make_config(self, tmp, name="prec"):
        return RepoConfig(
            name=name,
            source="github-release",
            url="https://github.com/jfut/prec",
            asset_include=r".*\.rpm$",
            enabled=True,
            minimum_release_age=0,
            cache_dir=tmp,
            refresh_interval=600,
        )

    def test_minimum_release_age(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp)
            config.minimum_release_age = 3600
            provider = GitHubReleaseProvider(config)
            young = {
                "published_at": (
                    datetime.now(timezone.utc) - timedelta(seconds=60)
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            }
            old = {
                "published_at": (
                    datetime.now(timezone.utc) - timedelta(seconds=7200)
                ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            }
            self.assertFalse(provider._release_is_old_enough(young))
            self.assertTrue(provider._release_is_old_enough(old))

    def test_matching_assets(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp)
            config.asset_include = r"\.rpm$"
            provider = GitHubReleaseProvider(config)
            assets = provider._matching_assets(
                {"assets": [{"name": "prec.rpm"}, {"name": "checksums.txt"}]}
            )
            self.assertEqual([asset["name"] for asset in assets], ["prec.rpm"])

    def test_validate_accepts_a_published_release_with_matching_rpm(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            with mock.patch.object(
                provider,
                "_request_json",
                return_value=[{"draft": False, "prerelease": False, "assets": [{"name": "prec.rpm"}]}],
            ):
                provider.validate()

    def test_validate_rejects_releases_without_matching_rpm(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            with mock.patch.object(
                provider,
                "_request_json",
                return_value=[{"draft": False, "prerelease": False, "assets": []}] * 100,
            ) as request_mock:
                with self.assertRaisesRegex(
                    ProviderError, "no compatible RPM assets found in the latest 100"
                ):
                    provider.validate()
            self.assertEqual(request_mock.call_count, 1)
            self.assertIn("page=1", request_mock.call_args.args[0])

    def test_matching_assets_rejects_path_traversal_asset_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            with self.assertRaises(ProviderError):
                provider._matching_assets({"assets": [{"name": "../outside.rpm"}]})

    def test_refresh_keeps_existing_cache_when_no_release_is_old_enough(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp)
            config.minimum_release_age = 3600
            os.makedirs(os.path.join(config.cache_path, "repodata"))
            os.makedirs(os.path.join(config.cache_path, "packages"))
            with open(
                os.path.join(config.cache_path, "packages", "prec-0.2.1-1.x86_64.rpm"),
                "wb",
            ) as fh:
                fh.write(b"rpm")
            save_state(
                config.cache_path,
                {
                    "asset_names": ["prec-0.2.1-1.x86_64.rpm"],
                    "last_refresh_at": "2026-06-21T00:00:00Z",
                },
            )
            provider = GitHubReleaseProvider(config)
            release = None

            with mock.patch.object(provider, "_fetch_latest_eligible_release", return_value=release):
                self.assertFalse(provider.refresh())

            self.assertTrue(os.path.isdir(config.cache_path))
            self.assertTrue(os.path.isdir(os.path.join(config.cache_path, "repodata")))

    def test_matching_assets_excludes_debug_and_source_rpms_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp)
            config.asset_include = r"\.rpm$"
            provider = GitHubReleaseProvider(config)
            assets = provider._matching_assets(
                {
                    "assets": [
                        {"name": "tool-1.0-1.x86_64.rpm"},
                        {"name": "tool-debuginfo-1.0-1.x86_64.rpm"},
                        {"name": "tool-debugsource-1.0-1.x86_64.rpm"},
                        {"name": "tool-1.0-1.src.rpm"},
                    ]
                }
            )
            self.assertEqual([asset["name"] for asset in assets], ["tool-1.0-1.x86_64.rpm"])

    def test_matching_debug_and_source_assets_use_auxiliary_repositories(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp, name="tool")
            config.asset_include = r"\.rpm$"
            config.arch = "x86_64"
            config.releasever = "el9"
            provider = GitHubReleaseProvider(config)
            release = {
                "assets": [
                    {"name": "tool-1.0-1.module_el9.1.2.x86_64.rpm"},
                    {"name": "tool-debuginfo-1.0-1.module_el9.1.2.x86_64.rpm"},
                    {"name": "tool-debugsource-1.0-1.module_el9.1.2.x86_64.rpm"},
                    {"name": "tool-1.0-1.module_el9.1.2.src.rpm"},
                    {"name": "tool-1.0-1.el10.x86_64.rpm"},
                ]
            }

            self.assertEqual(
                [asset["name"] for asset in provider._matching_debug_assets(release)],
                [
                    "tool-debuginfo-1.0-1.module_el9.1.2.x86_64.rpm",
                    "tool-debugsource-1.0-1.module_el9.1.2.x86_64.rpm",
                ],
            )
            self.assertEqual(
                [asset["name"] for asset in provider._matching_source_assets(release)],
                ["tool-1.0-1.module_el9.1.2.src.rpm"],
            )

    def test_matching_assets_allows_override_of_default_asset_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp)
            config.asset_include = r"\.rpm$"
            config.asset_exclude = r"$^"
            provider = GitHubReleaseProvider(config)
            assets = provider._matching_assets(
                {
                    "assets": [
                        {"name": "tool-1.0-1.x86_64.rpm"},
                        {"name": "tool-debuginfo-1.0-1.x86_64.rpm"},
                    ]
                }
            )
            self.assertEqual(
                [asset["name"] for asset in assets],
                [
                    "tool-1.0-1.x86_64.rpm",
                    "tool-debuginfo-1.0-1.x86_64.rpm",
                ],
            )

    def test_matching_assets_honors_arch_and_releasever(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp, name="tool")
            config.asset_include = r"\.rpm$"
            config.arch = "x86_64"
            config.releasever = "el9"
            provider = GitHubReleaseProvider(config)
            assets = provider._matching_assets(
                {
                    "assets": [
                        {"name": "tool-1.0-1.el9.x86_64.rpm"},
                        {"name": "tool-1.0-1.el9.noarch.rpm"},
                        {"name": "tool-1.0-1.el8.x86_64.rpm"},
                        {"name": "tool-1.0-1.el10.x86_64.rpm"},
                        {"name": "tool-1.0-1.el9.aarch64.rpm"},
                        {"name": "single-1.0-1.x86_64.rpm"},
                    ]
                }
            )
            self.assertEqual(
                [asset["name"] for asset in assets],
                [
                    "tool-1.0-1.el9.x86_64.rpm",
                    "tool-1.0-1.el9.noarch.rpm",
                    "single-1.0-1.x86_64.rpm",
                ],
            )

    def test_matching_assets_falls_back_to_nearest_lower_releasever(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp, name="tool")
            config.asset_include = r"\.rpm$"
            config.arch = "x86_64"
            config.releasever = "el10"
            provider = GitHubReleaseProvider(config)
            assets = provider._matching_assets(
                {
                    "assets": [
                        {"name": "tool-1.0-1.el8.x86_64.rpm"},
                        {"name": "tool-1.0-1.el9.x86_64.rpm"},
                        {"name": "tool-1.0-1.el11.x86_64.rpm"},
                        {"name": "single-1.0-1.x86_64.rpm"},
                    ]
                }
            )
            self.assertEqual(
                [asset["name"] for asset in assets],
                [
                    "tool-1.0-1.el9.x86_64.rpm",
                    "single-1.0-1.x86_64.rpm",
                ],
            )

    def test_matching_assets_uses_older_releasever_when_only_older_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp, name="tool")
            config.asset_include = r"\.rpm$"
            config.arch = "x86_64"
            config.releasever = "el9"
            provider = GitHubReleaseProvider(config)
            assets = provider._matching_assets(
                {
                    "assets": [
                        {"name": "tool-1.0-1.el8.x86_64.rpm"},
                        {"name": "tool-1.0-1.el10.x86_64.rpm"},
                    ]
                }
            )
            self.assertEqual(
                [asset["name"] for asset in assets],
                ["tool-1.0-1.el8.x86_64.rpm"],
            )

    def test_matching_assets_excludes_older_module_release_when_exact_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.make_config(tmp, name="nginx-module-fancyindex")
            config.asset_include = r"\.rpm$"
            config.arch = "x86_64"
            config.releasever = "el10"
            provider = GitHubReleaseProvider(config)
            assets = provider._matching_assets(
                {
                    "assets": [
                        {"name": "nginx-module-fancyindex-0.5.2-8.el10.x86_64.rpm"},
                        {"name": "nginx-module-fancyindex-0.5.2-8.module_el9.1.26.x86_64.rpm"},
                        {"name": "nginx-module-fancyindex-debuginfo-0.5.2-8.el10.x86_64.rpm"},
                        {"name": "nginx-module-fancyindex-debuginfo-0.5.2-8.module_el9.1.26.x86_64.rpm"},
                    ]
                }
            )
            self.assertEqual(
                [asset["name"] for asset in assets],
                [
                    "nginx-module-fancyindex-0.5.2-8.el10.x86_64.rpm",
                ],
            )

    def test_request_json_retries_transient_http_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            response = mock.Mock()
            response.read.return_value = b'{"tag_name": "v1"}'
            response.__enter__ = mock.Mock(return_value=response)
            response.__exit__ = mock.Mock(return_value=False)
            transient = urllib.error.HTTPError(
                url="https://api.github.com/repos/jfut/prec/releases/latest",
                code=504,
                msg="Gateway Timeout",
                hdrs=None,
                fp=None,
            )
            with mock.patch("time.sleep") as sleep_mock:
                with mock.patch(
                    "urllib.request.urlopen",
                    side_effect=[transient, response],
                ) as urlopen_mock:
                    data = provider._request_json(
                        "https://api.github.com/repos/jfut/prec/releases/latest"
                    )
            self.assertEqual(data["tag_name"], "v1")
            self.assertEqual(urlopen_mock.call_count, 2)
            sleep_mock.assert_called_once_with(1.0)

    def test_fetch_latest_eligible_release_skips_too_new_latest(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            provider.config.minimum_release_age = 3 * 86400
            too_new = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            old_enough = (datetime.now(timezone.utc) - timedelta(days=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            response = mock.Mock()
            response.read.return_value = (
                (
                    '[{"tag_name":"v2","published_at":"%s","assets":[]},'
                    '{"tag_name":"v1","published_at":"%s","assets":[]}]'
                )
                % (too_new, old_enough)
            ).encode("utf-8")
            response.__enter__ = mock.Mock(return_value=response)
            response.__exit__ = mock.Mock(return_value=False)
            with mock.patch("urllib.request.urlopen", return_value=response):
                release = provider._fetch_latest_eligible_release()
            self.assertEqual(release["tag_name"], "v1")

    def test_fetch_latest_eligible_release_returns_none_when_no_release_is_old_enough(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            provider.config.minimum_release_age = 3 * 86400
            too_new = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            response = mock.Mock()
            response.read.return_value = (
                '[{"tag_name":"v2","published_at":"%s","assets":[]},'
                '{"tag_name":"v-next","draft":true,"published_at":"2020-01-01T00:00:00Z","assets":[]}]'
                % too_new
            ).encode("utf-8")
            response.__enter__ = mock.Mock(return_value=response)
            response.__exit__ = mock.Mock(return_value=False)
            with mock.patch("urllib.request.urlopen", return_value=response):
                release = provider._fetch_latest_eligible_release()
            self.assertIsNone(release)

    def test_fetch_latest_eligible_release_checks_additional_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            provider.config.minimum_release_age = 3 * 86400
            too_new = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            old_enough = (datetime.now(timezone.utc) - timedelta(days=10)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            first_page = [
                {
                    "tag_name": f"v-next-{index}",
                    "published_at": too_new,
                    "assets": [],
                }
                for index in range(100)
            ]
            second_page = [
                {
                    "tag_name": "v1",
                    "published_at": old_enough,
                    "assets": [],
                }
            ]
            with mock.patch.object(provider, "_request_json", side_effect=[first_page, second_page]) as request_mock:
                release = provider._fetch_latest_eligible_release()
            self.assertEqual(release["tag_name"], "v1")
            self.assertEqual(request_mock.call_count, 2)

    def test_request_json_includes_github_error_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            error = urllib.error.HTTPError(
                url="https://api.github.com/repos/jfut/prec/releases/latest",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"message":"API rate limit exceeded for 203.0.113.10.","documentation_url":"https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting"}'
                ),
            )
            with mock.patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaises(ProviderError) as ctx:
                    provider._request_json("https://api.github.com/repos/jfut/prec/releases/latest")
            self.assertEqual(
                str(ctx.exception),
                "prec: GitHub API returned HTTP 403: API rate limit exceeded for 203.0.113.10.",
            )

    def test_request_json_appends_rate_limit_reset_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            error = urllib.error.HTTPError(
                url="https://api.github.com/repos/jfut/prec/releases/latest",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"message":"API rate limit exceeded for 203.0.113.10.","documentation_url":"https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting"}'
                ),
            )
            rate_limit_response = mock.Mock()
            rate_limit_response.read.return_value = (
                b'{"rate":{"limit":60,"remaining":37,"reset":1782456927,"used":23,"resource":"core"}}'
            )
            rate_limit_response.__enter__ = mock.Mock(return_value=rate_limit_response)
            rate_limit_response.__exit__ = mock.Mock(return_value=False)
            with mock.patch(
                "urllib.request.urlopen",
                side_effect=[error, rate_limit_response],
            ):
                with self.assertRaises(ProviderError) as ctx:
                    provider._request_json("https://api.github.com/repos/jfut/prec/releases/latest")
            expected_reset_at = (
                datetime.fromtimestamp(1782456927, tz=timezone.utc)
                .astimezone()
                .strftime("%Y-%m-%d %H:%M:%S %Z")
            )
            self.assertEqual(
                str(ctx.exception),
                f"prec: GitHub API returned HTTP 403: API rate limit exceeded for 203.0.113.10.\nThe rate limit will reset at {expected_reset_at}.",
            )

    def test_request_json_ignores_rate_limit_reset_lookup_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            provider = GitHubReleaseProvider(self.make_config(tmp))
            error = urllib.error.HTTPError(
                url="https://api.github.com/repos/jfut/prec/releases/latest",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=io.BytesIO(b'{"message":"API rate limit exceeded."}'),
            )
            rate_limit_error = urllib.error.HTTPError(
                url="https://api.github.com/rate_limit",
                code=403,
                msg="Forbidden",
                hdrs=None,
                fp=io.BytesIO(b""),
            )
            with mock.patch(
                "urllib.request.urlopen",
                side_effect=[error, rate_limit_error],
            ):
                with self.assertRaises(ProviderError) as ctx:
                    provider._request_json("https://api.github.com/repos/jfut/prec/releases/latest")
            self.assertEqual(
                str(ctx.exception),
                "prec: GitHub API returned HTTP 403: API rate limit exceeded.",
            )

    def test_replace_cache_keeps_existing_cache_on_download_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "prec")
            packages_path = os.path.join(cache_path, "packages")
            os.makedirs(packages_path)
            os.makedirs(os.path.join(cache_path, "repodata"))
            old_package = os.path.join(packages_path, "old.rpm")
            with open(old_package, "wb") as fh:
                fh.write(b"old")

            config = self.make_config(tmp)
            config.asset_include = r"\.rpm$"
            provider = GitHubReleaseProvider(config)
            provider.release = {"id": 1, "tag_name": "v1"}
            provider.assets = [
                {
                    "id": 1,
                    "name": "new.rpm",
                    "updated_at": "2026-06-21T00:00:00Z",
                    "browser_download_url": "https://example.invalid/new.rpm",
                }
            ]

            with mock.patch.object(
                provider,
                "_download_file",
                side_effect=ProviderError("download failed"),
            ):
                with self.assertRaises(ProviderError):
                    provider._replace_cache()

            self.assertTrue(os.path.isfile(old_package))
            self.assertTrue(os.path.isdir(os.path.join(cache_path, "repodata")))


if __name__ == "__main__":
    unittest.main()
