"""Static guards for the no-rebuild Gate 6C release promotion path."""

import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
RELEASE_COMMIT = "d03c7eab7e3e10afc2f62662d25ccd63427c22e9"


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _signed_wrapper_fixture() -> bytes:
    where_exe = shutil.which("where.exe")
    assert where_exe, "The signed Windows where.exe fixture is required."
    return Path(where_exe).read_bytes()


def _latest_yml(*, release_date_after_packages: bool = False) -> str:
    wrapper = _signed_wrapper_fixture()
    payload = b"payload fixture"
    wrapper_sha512 = base64.b64encode(hashlib.sha512(wrapper).digest()).decode()
    payload_sha512 = base64.b64encode(hashlib.sha512(payload).digest()).decode()
    release_date = "releaseDate: '2026-07-29T18:45:18.746Z'\n"
    packages = (
        "packages:\n"
        "  x64:\n"
        f"    size: {len(payload)}\n"
        f"    sha512: {payload_sha512}\n"
        "    blockMapSize: 123\n"
        "    path: murmur-1.2.3-x64.nsis.7z\n"
        "    file: murmur-1.2.3-x64.nsis.7z\n"
    )
    top = (
        "version: 1.2.3\n"
        "files:\n"
        "  - url: Eve.Web.Setup.1.2.3.exe\n"
        f"    sha512: {wrapper_sha512}\n"
        "path: Eve.Web.Setup.1.2.3.exe\n"
        f"sha512: {wrapper_sha512}\n"
    )
    if release_date_after_packages:
        return top + packages + release_date
    return top + release_date + packages


def _write_release_fixture(directory: Path, latest: str) -> str:
    files = {
        "Eve.Web.Setup.1.2.3.exe": _signed_wrapper_fixture(),
        "murmur-1.2.3-x64.nsis.7z": b"payload fixture",
        "latest.yml": latest.encode(),
        "THIRD_PARTY_NOTICES.txt": b"Generated from the exact pre-package closure\n",
    }
    for name, content in files.items():
        (directory / name).write_bytes(content)

    base_names = tuple(files)
    for algorithm in ("sha256", "sha512"):
        lines = [f"{_digest(directory / name, algorithm)} *{name}" for name in base_names]
        (directory / f"{algorithm.upper()}SUMS.txt").write_text(
            "\n".join(lines) + "\n", encoding="ascii"
        )

    asset_names = (*base_names, "SHA256SUMS.txt", "SHA512SUMS.txt")
    manifest = {
        "schema": 1,
        "tag": "v1.2.3",
        "commit": RELEASE_COMMIT,
        "version": "1.2.3",
        "assets": [
            {
                "name": name,
                "bytes": (directory / name).stat().st_size,
                "sha256": _digest(directory / name, "sha256"),
                "sha512": _digest(directory / name, "sha512"),
            }
            for name in asset_names
        ],
    }
    manifest_path = directory / "eve-v1.2.3-artifact-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return _digest(manifest_path, "sha256")


def _verify_release_fixture(
    directory: Path, manifest_sha256: str
) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh")
    assert pwsh, "PowerShell 7 is required for release-control tests."
    return subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "release-artifacts.ps1"),
            "-Mode",
            "Verify",
            "-ArtifactDir",
            str(directory),
            "-ExpectedTag",
            "v1.2.3",
            "-ExpectedCommit",
            RELEASE_COMMIT,
            "-ExpectedManifestSha256",
            manifest_sha256,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _create_release_fixture(directory: Path) -> subprocess.CompletedProcess[str]:
    pwsh = shutil.which("pwsh")
    assert pwsh, "PowerShell 7 is required for release-control tests."
    return subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(ROOT / "scripts" / "release-artifacts.ps1"),
            "-Mode",
            "Create",
            "-ArtifactDir",
            str(directory),
            "-ExpectedTag",
            "v1.2.3",
            "-ExpectedCommit",
            RELEASE_COMMIT,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_release_workflow_is_manual_and_never_builds_or_uploads() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    dispatch_block = workflow.split("workflow_dispatch:", 1)[1].split("permissions:", 1)[0]
    risk_gate_step = workflow.split("- name: Require explicit risk gates", 1)[1].split(
        "- name: Download existing draft assets only", 1
    )[0]
    assert dispatch_block
    assert "push:" not in workflow
    for forbidden in ("package:win", "electron-builder", "bun install", "softprops/action-gh-release", "gh release upload", "gh release create"):
        assert forbidden not in workflow
    assert "production-release" in workflow
    assert "allow_unsigned" in workflow
    assert "accepted_name_risk" in workflow
    assert "publish_prerelease:" in dispatch_block
    allow_block = workflow.split("allow_unsigned:", 1)[1].split("accepted_name_risk:", 1)[0]
    name_block = workflow.split("accepted_name_risk:", 1)[1].split("permissions:", 1)[0]
    assert "default: false" in allow_block
    assert "default: false" in name_block
    assert "draft=false" in workflow
    assert "PUBLISH_PRERELEASE: ${{ inputs.publish_prerelease }}" in risk_gate_step
    assert "Prerelease intent must match the semantic-version tag." in risk_gate_step


def test_release_asset_contract_and_notice_generator_are_tracked() -> None:
    artifacts = (ROOT / "scripts/release-artifacts.ps1").read_text(encoding="utf-8")
    notices = (ROOT / "scripts/generate-third-party-notices.ps1").read_text(encoding="utf-8")
    for name in ("Eve.Web.Setup.$version.exe", "murmur-$version-x64.nsis.7z", "latest.yml", "SHA256SUMS.txt", "SHA512SUMS.txt", "THIRD_PARTY_NOTICES.txt"):
        assert name in artifacts
    assert (
        'foreach($file in $files){if($file.Length -ge 2100000000){throw '
        '"Asset exceeds 2.10 GB safety ceiling: $($file.Name)"}}'
    ) in artifacts
    assert '$payload=Get-Item (Join-Path $dir "murmur-$version-x64.nsis.7z")' in artifacts
    assert (
        'if($payload.Length -ge 2050000000){throw '
        '"NSIS-web payload exceeds 2.05 GB release target: $($payload.Name)"}'
    ) in artifacts
    assert "NotSigned" in artifacts
    assert "Authenticode must be Valid when allow_unsigned=false" in artifacts
    assert "Manifest schema, format, tag, commit, or version mismatch." in artifacts
    assert 'checksum does not match manifest.' in artifacts
    assert "Missing required latest.yml fields." in artifacts
    assert "Unknown or malformed latest.yml line" in artifacts
    assert "latest.yml hash or size mismatch." in artifacts
    assert "Unclassified shipped component" in notices
    assert "THIRD_PARTY_INVENTORY.json" in notices
    assert "Get-ChildItem $dist.FullName -Recurse -File" in notices
    assert "the mit license" in notices.lower()
    assert "$Matches[1].Trim()" in notices
    assert "ToLowerInvariant" in notices
    assert "unknown|none|n/?a" in notices
    assert "$isBsd3" in notices
    assert "neither the name.*endorse or promote" in notices
    assert "Reviewed override SHA-256 mismatch" in notices
    assert "Get-StableSource" in notices
    assert "Packaged native DLL inventory is empty." in notices
    assert "Configured native notice absent from packaged DLL inventory" in notices


def test_release_workflow_maps_inputs_and_preserves_release_boundaries() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    verify_draft = workflow.split("  verify-draft:\n", 1)[1].split("\n  promote:", 1)[0]
    assert "permissions:\n      contents: write" in verify_draft
    for forbidden in (
        "gh release edit",
        "gh release upload",
        "gh release create",
        "gh release delete",
        "gh api --method",
        "gh api -X",
        "curl.exe -X",
        "curl.exe --request",
        "--data",
        "--form",
        "git tag",
        "git update-ref",
        "git replace",
        "git push",
    ):
        assert forbidden not in verify_draft
    assert workflow.count("persist-credentials: false") == 2
    assert workflow.count("ref: ${{ github.sha }}") == 2
    assert "ref: ${{ inputs.expected_commit }}" not in workflow
    assert workflow.count("$head -ne $env:GITHUB_SHA") == 2
    assert workflow.count("$env:GITHUB_REF -ne 'refs/heads/trunk'") == 2
    assert workflow.count("Release tag commit does not match ExpectedCommit.") == 2
    assert "expected_release_id:" in workflow
    assert workflow.count("$env:EXPECTED_RELEASE_ID -notmatch '^[1-9][0-9]*$'") == 2
    assert workflow.count('gh api "repos/${{ github.repository }}/releases/$env:EXPECTED_RELEASE_ID"') == 2
    assert workflow.count("[string]$release.id -ne $env:EXPECTED_RELEASE_ID") == 2
    assert workflow.count("$release.tag_name -ne $env:TAG") == 2
    assert workflow.count("$release.target_commitish -ne $env:EXPECTED_COMMIT") == 2
    prerelease_check = (
        "[bool]$release.prerelease -ne ($env:PUBLISH_PRERELEASE -eq 'true')"
    )
    assert verify_draft.count(prerelease_check) == 1
    assert workflow.count("$assets.Count -ne $expectedNames.Count") == 2
    assert workflow.count("'Accept: application/octet-stream'") == 2
    assert workflow.count('"Authorization: Bearer $env:GH_TOKEN"') == 2
    assert workflow.count("--output (Join-Path release-assets $asset.name)") == 2
    assert workflow.count("$download.Length -ne [int64]$asset.size") == 2
    assert workflow.count("$downloadDigest -ne $asset.digest") == 2
    verifier = "& .\\scripts\\release-artifacts.ps1 -Mode Verify"
    assert workflow.count(verifier) == 2
    assert "Artifact verification failed." not in workflow
    assert "Artifact re-verification failed." not in workflow
    verify_manifest_step = workflow.split(
        "- name: Verify draft manifest and artifacts", 1
    )[1].split("\n  promote:", 1)[0]
    assert "$ErrorActionPreference = 'Stop'" in verify_manifest_step
    assert verifier in verify_manifest_step
    promote_step = workflow.split(
        "- name: Re-download and reverify after approval", 1
    )[1].split("- name: Publish the existing verified draft only", 1)[0]
    assert (
        "EXPECTED_RELEASE_ID: ${{ inputs.expected_release_id }}" in promote_step
    )
    assert "PUBLISH_PRERELEASE: ${{ inputs.publish_prerelease }}" in promote_step
    assert promote_step.count(prerelease_check) == 1
    assert "$ErrorActionPreference = 'Stop'" in promote_step
    assert verifier in promote_step
    assert "gh release view" not in workflow
    assert "gh release download" not in workflow
    assert "EXPECTED_MANIFEST_SHA256" in workflow
    assert "-AllowUnsigned:($env:ALLOW_UNSIGNED -eq 'true')" in workflow
    publish_step = workflow.split(
        "- name: Publish the existing verified draft only", 1
    )[1]
    assert "gh release edit $env:TAG" in publish_step
    assert "--draft=false --prerelease --latest=false" in publish_step
    assert "--draft=false --prerelease=false --latest" in publish_step
    assert "gh release upload" not in workflow
    assert "gh release create" not in workflow


def test_packaged_resource_and_runtime_harness_guards() -> None:
    package = (ROOT / "app/package.json").read_text(encoding="utf-8")
    verify = (ROOT / "scripts/release-verify.ps1").read_text(encoding="utf-8")
    assert '"package": "bun run notices:generate && bun run build && electron-builder"' in package
    assert '"to": "legal"' in package
    assert ".runtime\\python.exe" in verify
    assert "import faster_whisper, torch" in verify
    assert "nemo.collections.asr" not in verify
    assert "Deferred Nemotron packages" in verify
    assert 'Remove-Item -Path "env:$key"' in verify


@pytest.mark.parametrize("release_date_after_packages", [False, True])
def test_release_artifacts_accepts_electron_builder_latest_yml_ordering(
    tmp_path: Path, release_date_after_packages: bool
) -> None:
    manifest_sha256 = _write_release_fixture(
        tmp_path, _latest_yml(release_date_after_packages=release_date_after_packages)
    )
    result = _verify_release_fixture(tmp_path, manifest_sha256)
    assert result.returncode == 0, result.stdout + result.stderr


def test_release_artifacts_create_generates_and_verifies_manifest_from_six_inputs(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "eve-v1.2.3-artifact-manifest.json"
    _write_release_fixture(tmp_path, _latest_yml())
    manifest_path.unlink()

    create = _create_release_fixture(tmp_path)

    assert create.returncode == 0, create.stdout + create.stderr
    assert manifest_path.is_file()
    verify = _verify_release_fixture(tmp_path, _digest(manifest_path, "sha256"))
    assert verify.returncode == 0, verify.stdout + verify.stderr


@pytest.mark.parametrize(
    ("mutate", "expected_error"),
    [
        (
            lambda latest: latest.replace(
                "    size: 15\n", "    size: 15\n    size: 15\n"
            ),
            "Malformed or duplicate latest.yml package property.",
        ),
        (
            lambda latest: latest.replace("    size: 15\n", "   size: 15\n"),
            "Unknown or malformed latest.yml line",
        ),
        (
            lambda latest: latest.replace("    size: 15\n", "    size: 16\n"),
            "latest.yml hash or size mismatch.",
        ),
    ],
)
def test_release_artifacts_rejects_adversarial_package_fields(
    tmp_path: Path, mutate, expected_error: str
) -> None:
    manifest_sha256 = _write_release_fixture(tmp_path, mutate(_latest_yml()))
    result = _verify_release_fixture(tmp_path, manifest_sha256)
    assert result.returncode != 0
    assert expected_error in result.stdout + result.stderr
