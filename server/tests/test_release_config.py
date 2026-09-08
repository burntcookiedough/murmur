"""Tests for release/build configuration expectations."""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def _load_package_json() -> dict:
    package_path = ROOT / "app" / "package.json"
    with package_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_server_pyproject() -> dict:
    with (ROOT / "server" / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _load_server_lock() -> dict:
    with (ROOT / "server" / "uv.lock").open("rb") as handle:
        return tomllib.load(handle)


def _extract_targets(target_value: object) -> list[str]:
    if isinstance(target_value, str):
        return [target_value]
    targets: list[str] = []
    if isinstance(target_value, list):
        for entry in target_value:
            if isinstance(entry, str):
                targets.append(entry)
            elif isinstance(entry, dict):
                target = entry.get("target")
                if isinstance(target, str):
                    targets.append(target)
    return targets


def _extract_publish_providers(publish_value: object) -> list[str]:
    providers: list[str] = []
    if isinstance(publish_value, dict):
        provider = publish_value.get("provider")
        if isinstance(provider, str):
            providers.append(provider)
    if isinstance(publish_value, list):
        for entry in publish_value:
            if isinstance(entry, dict):
                provider = entry.get("provider")
                if isinstance(provider, str):
                    providers.append(provider)
    return providers


def test_electron_builder_windows_target_is_nsis_web() -> None:
    package_json = _load_package_json()
    win_config = package_json.get("build", {}).get("win", {})
    targets = _extract_targets(win_config.get("target"))
    assert "nsis-web" in targets


def test_nsis_web_identity_and_data_retention_are_explicit() -> None:
    package_json = _load_package_json()
    build = package_json.get("build", {})
    nsis_web = build.get("nsisWeb", {})

    assert package_json.get("name") == "murmur"
    assert build.get("appId") == "io.github.burntcookiedough.eve"
    assert build.get("productName") == "Eve"
    assert build.get("executableName") == "Eve"
    assert nsis_web == {
        "guid": "0204d005-75b3-5b31-b1f6-ef2831e2b204",
        "oneClick": True,
        "deleteAppDataOnUninstall": False,
        "artifactName": "Eve.Web.Setup.${version}.${ext}",
        "shortcutName": "Eve",
        "uninstallDisplayName": "Eve ${version}",
    }


def test_main_build_enters_through_identity_bootstrap() -> None:
    build_script = (ROOT / "app" / "scripts" / "build-main.js").read_text(
        encoding="utf-8"
    )
    bootstrap = (ROOT / "app" / "src" / "main" / "bootstrap.ts").read_text(
        encoding="utf-8"
    )
    bootstrap_core = (
        ROOT / "app" / "src" / "main" / "bootstrap-core.ts"
    ).read_text(encoding="utf-8")

    assert "src/main/bootstrap.ts" in build_script
    assert "requestSingleInstanceLock" in bootstrap_core
    assert "setPath('userData'" in bootstrap_core
    assert "import('./index.js')" in bootstrap
    assert "await bootstrapApplication" in bootstrap


def test_electron_builder_publish_includes_github() -> None:
    package_json = _load_package_json()
    publish = package_json.get("build", {}).get("publish")
    providers = _extract_publish_providers(publish)
    assert "github" in providers


def test_electron_builder_publish_targets_release_repository() -> None:
    package_json = _load_package_json()
    publish = package_json.get("build", {}).get("publish")
    github_publishers = [
        entry
        for entry in publish
        if isinstance(entry, dict) and entry.get("provider") == "github"
    ]
    assert github_publishers == [
        {
            "provider": "github",
            "owner": "burntcookiedough",
            "repo": "eve-windows-dictation",
        }
    ]


def test_windows_packaging_never_publishes_implicitly() -> None:
    package_json = _load_package_json()
    package_script = package_json.get("scripts", {}).get("package:win", "")
    assert "--publish never" in package_script


def test_release_verification_targets_eve_with_legacy_payload_name() -> None:
    contents = (ROOT / "scripts" / "release-verify.ps1").read_text(
        encoding="utf-8"
    )
    assert '"Eve.Web.Setup.$ExpectedVersion.exe"' in contents
    assert '"Eve.exe"' in contents
    assert '"murmur-$ExpectedVersion-x64.nsis.7z"' in contents


def test_release_sync_pins_python_excludes_dev_and_checks_runtime_abi() -> None:
    release_sync = "uv sync --python 3.11 --no-dev --extra release --frozen"
    for relative_path in (
        "AGENTS.md",
        "README.md",
        "docs/development/building.md",
        "docs/installer-dependencies.md",
    ):
        assert release_sync in (ROOT / relative_path).read_text(encoding="utf-8")

    runtime_script = (ROOT / "scripts" / "prepare-python-runtime.ps1").read_text(
        encoding="utf-8"
    )
    assert 'Join-Path $serverPath ".venv\\Scripts\\python.exe"' in runtime_script
    assert "$venvAbi = Get-PythonAbi -PythonPath $venvPython" in runtime_script
    assert "Release .venv/runtime Python ABI mismatch" in runtime_script
    mismatch_index = runtime_script.index("Release .venv/runtime Python ABI mismatch")
    replace_index = runtime_script.index(
        'Remove-Item -LiteralPath $runtimePath -Recurse -Force'
    )
    assert mismatch_index < replace_index


def test_release_extra_is_whisper_torch_only_and_all_keeps_nemotron() -> None:
    project = _load_server_pyproject()["project"]
    extras = project["optional-dependencies"]

    assert extras["release"] == ["murmur[whisper]", "torch>=2.0"]
    assert extras["all"] == ["murmur[whisper,nemotron]"]
    assert "nemo_toolkit[asr]>=2.2.0" in extras["nemotron"]
    assert "torchaudio>=2.0" in extras["nemotron"]

    murmur = next(
        package
        for package in _load_server_lock()["package"]
        if package["name"] == "murmur"
    )
    assert murmur["optional-dependencies"]["release"] == [
        {"name": "faster-whisper"},
        {"name": "torch"},
    ]

    locked_versions = {
        package["name"]: package["version"]
        for package in _load_server_lock()["package"]
        if package["name"] in {"faster-whisper", "torch"}
    }
    assert locked_versions == {
        "faster-whisper": "1.2.1",
        "torch": "2.6.0+cu124",
    }


def test_release_verification_requires_only_the_shipped_engine_closure() -> None:
    contents = (ROOT / "scripts" / "release-verify.ps1").read_text(
        encoding="utf-8"
    )
    assert 'Join-Path $sitePackages "faster_whisper"' in contents
    assert 'Join-Path $sitePackages "torch"' in contents
    assert "import faster_whisper, torch" in contents
    assert "discover_engines" in contents
    assert "Deferred Nemotron packages" in contents
    assert "torchaudio" in contents
    assert "nemo.collections.asr" not in contents


def test_release_verification_requires_both_engine_discovery_properties() -> None:
    contents = (ROOT / "scripts" / "release-verify.ps1").read_text(
        encoding="utf-8"
    )
    required_properties = '$requiredEngineProperties = @("whisper", "nemotron")'
    missing_properties = "$missingEngineProperties"
    missing_guard = "if ($missingEngineProperties.Count -gt 0) {"
    missing_throw = 'throw "Packaged engine discovery omitted required properties: $($missingEngineProperties -join \', \')"'
    whisper_availability = "$whisperAvailable = [bool]$discovery.whisper"

    assert required_properties in contents
    assert missing_properties in contents
    assert missing_guard in contents
    assert missing_throw in contents
    missing_properties_index = contents.index(missing_properties)
    missing_guard_index = contents.index(missing_guard, missing_properties_index)
    missing_throw_index = contents.index(missing_throw, missing_guard_index)
    whisper_availability_index = contents.index(whisper_availability)
    assert missing_properties_index < missing_guard_index < missing_throw_index < whisper_availability_index


def test_release_workflow_verifies_existing_draft_without_rebuilding() -> None:
    workflow_path = ROOT / ".github" / "workflows" / "release.yml"
    contents = workflow_path.read_text(encoding="utf-8")
    publish_step = contents.split(
        "- name: Publish the existing verified draft only", 1
    )[1]
    assert "workflow_dispatch:" in contents
    assert "production-release" in contents
    assert "publish_prerelease" in contents
    assert "release-artifacts.ps1 -Mode Verify" in contents
    assert 'gh api "repos/${{ github.repository }}/releases/$env:EXPECTED_RELEASE_ID"' in contents
    assert "'Accept: application/octet-stream'" in contents
    assert "gh release download" not in contents
    assert "draft=false" in contents
    assert "PUBLISH_PRERELEASE: ${{ inputs.publish_prerelease }}" in publish_step
    assert "--draft=false --prerelease --latest=false" in publish_step
    assert "--draft=false --prerelease=false --latest" in publish_step
    allow_block = contents.split("allow_unsigned:", 1)[1].split("accepted_name_risk:", 1)[0]
    name_block = contents.split("accepted_name_risk:", 1)[1].split("permissions:", 1)[0]
    assert "default: false" in allow_block
    assert "default: false" in name_block
    for forbidden in ("push:", "package:win", "electron-builder", "softprops/action-gh-release", "gh release upload"):
        assert forbidden not in contents


def test_packaging_includes_relocatable_runtime() -> None:
    package_json = _load_package_json()
    resources = package_json.get("build", {}).get("extraResources", [])
    filters = [
        entry
        for resource in resources
        if isinstance(resource, dict)
        for entry in resource.get("filter", [])
        if isinstance(entry, str)
    ]
    assert ".runtime/**/*" in filters
    assert any(
        resource.get("from") == "resources/generated/legal"
        and resource.get("to") == "legal"
        for resource in resources
        if isinstance(resource, dict)
    )
    assert "!.venv/**/pip*" not in filters
    assert "!.venv/**/wheel*" not in filters
    assert "!.venv/**/setuptools*" not in filters
    assert "!.venv/Lib/site-packages/pip/**" in filters
    assert "!.venv/Lib/site-packages/wheel/**" in filters
    assert "!.venv/Lib/site-packages/setuptools/**" in filters
    excluded_patterns = [item[1:] for item in filters if item.startswith("!")]
    for test_ui_path in (
        ".venv/Lib/site-packages/PySide6/QtCore.pyd",
        ".venv/Lib/site-packages/PySide6_Addons-6.10.1.dist-info/METADATA",
        ".venv/Lib/site-packages/PySide6_Essentials-6.10.1.dist-info/METADATA",
        ".venv/Lib/site-packages/pyside6_addons-6.10.1.dist-info/METADATA",
        ".venv/Lib/site-packages/shiboken6-6.10.1.dist-info/METADATA",
    ):
        assert any(fnmatchcase(test_ui_path, pattern) for pattern in excluded_patterns)
    assert "!.venv/Lib/site-packages/**/test/**" in filters
    assert "!.venv/Lib/site-packages/**/tests/**" in filters
    assert "!.venv/Lib/site-packages/**/*.lib" in filters
    assert "!.venv/Lib/site-packages/torch/include/**" in filters
    assert "!.runtime/Lib/test/**" in filters


def test_bundled_defaults_are_hardware_neutral() -> None:
    settings = json.loads((ROOT / "server" / "settings.json").read_text(encoding="utf-8"))
    assert settings["engine_preference_mode"] == "auto"
    assert settings["whisper_device"] == "auto"
    assert settings["whisper_compute_type"] == "auto"


def test_ci_runs_app_and_server_tests() -> None:
    contents = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "bun test" in contents
    assert "uv run --no-sync pytest" in contents
    assert "timeout-minutes: 30" in contents


def test_server_manager_uses_portable_runtime_and_user_settings() -> None:
    contents = (ROOT / "app" / "src" / "main" / "services" / "server-manager.ts").read_text(
        encoding="utf-8"
    )
    assert "path.join(serverDir, '.runtime', 'python.exe')" in contents
    assert "path.join(serverDir, '.venv', 'Lib', 'site-packages')" in contents
    assert "PYTHONPATH" in contents
    assert "MURMUR_SETTINGS_FILE" in contents
    assert "path.join(app.getPath('userData'), 'server-settings.json')" in contents


def test_release_verify_uses_packaged_runtime_and_controlled_environment() -> None:
    contents = (ROOT / "scripts" / "release-verify.ps1").read_text(encoding="utf-8")
    assert '.runtime\\python.exe' in contents
    assert '.venv\\Lib\\site-packages' in contents
    assert "MURMUR_SETTINGS_FILE" in contents
    assert "MURMUR_WHISPER_COMPUTE_TYPE" in contents
    assert "function Assert-SelfContainedRuntime" in contents
    assert "ConvertFrom-Json" in contents
    assert "sys.path entry resolves outside the runtime" in contents
    assert "Assert-SelfContainedRuntime -RuntimePath" in contents


def test_runtime_preparation_script_uses_uv_managed_python() -> None:
    contents = (ROOT / "scripts" / "prepare-python-runtime.ps1").read_text(encoding="utf-8")
    assert "uv python install" in contents
    assert "uv python find --managed-python --no-project --resolve-links" in contents
    assert "Remove-Item Env:VIRTUAL_ENV" in contents
    assert "$env:VIRTUAL_ENV = $previousVirtualEnv" in contents
    assert "Test-PathWithin -Path $pythonPath -Root $serverPath" in contents
    assert r"\.venv" in contents
    assert "Scripts" in contents
    assert "$pythonAbi =" in contents
    assert '"python$pythonAbi.dll"' in contents
    for required in ('"python.exe"', '"Lib"', '"DLLs"'):
        assert required in contents
    assert "function Assert-SelfContainedRuntime" in contents
    assert "ConvertFrom-Json" in contents
    assert "sys.path entry resolves outside the runtime" in contents


def test_version_check_includes_all_release_metadata() -> None:
    version = _load_package_json()["version"]
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "version.py"),
            "check",
            "--tag",
            f"v{version}",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert f"Version check passed: {version}" in completed.stdout

    dry_run = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "version.py"),
            "bump",
            version,
            "--dry-run",
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    dry_run_output = dry_run.stdout.replace("\\", "/")
    for path in (
        "app/package.json",
        "server/pyproject.toml",
        "server/src/version.py",
        "server/uv.lock",
        "README.md",
        "scripts/release-verify.ps1",
    ):
        assert path in dry_run_output
