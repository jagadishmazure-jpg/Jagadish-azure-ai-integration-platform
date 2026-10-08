"""Section 36 - Reference architecture as code: Bicep compiles clean, cost-minimized defaults,
opt-in profiles default off, azd wiring matches the workloads, deploy workflow is gated."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infra"
BICEP = shutil.which("bicep") or (
    str(Path.home() / ".azure" / "bin" / "bicep")
    if (Path.home() / ".azure" / "bin" / "bicep").exists()
    else None
)


@pytest.mark.skipif(BICEP is None, reason="bicep CLI not installed")
def test_bicep_builds_without_errors_or_warnings(tmp_path):
    r = subprocess.run(
        [BICEP, "build", str(INFRA / "main.bicep"), "--outfile", str(tmp_path / "main.json")],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "Warning" not in r.stderr and "Error" not in r.stderr, r.stderr
    arm = json.loads((tmp_path / "main.json").read_text())
    assert arm["parameters"]["deployFrontDoor"]["defaultValue"] is False


def _main() -> str:
    return (INFRA / "main.bicep").read_text()


def test_cost_minimized_defaults():
    src = _main()
    assert "param apimSku string = 'Consumption'" in src
    assert "param serviceBusSku string = 'Basic'" in src
    assert "param logDailyQuotaGb int = 1" in src
    assert "param minReplicas int = 0" in src
    assert "param deployFrontDoor bool = false" in src
    assert "param privateNetworking bool = false" in src
    assert "param computeProfile string = 'containerapps'" in src
    fn = (INFRA / "modules" / "functions.bicep").read_text()
    assert "name: 'FC1', tier: 'FlexConsumption'" in fn and "version: '3.13'" in fn
    assert "workloadProfileType: 'Consumption'" in (INFRA / "modules" / "containerapps-env.bicep").read_text()
    assert "dailyQuotaGb: dailyQuotaGb" in (INFRA / "modules" / "monitoring.bicep").read_text()


def test_no_keys_or_connection_strings_for_data_plane():
    assert "disableLocalAuth: true" in (INFRA / "modules" / "servicebus.bicep").read_text()
    assert "disableLocalAuth: true" in (INFRA / "modules" / "eventgrid.bicep").read_text()
    assert "allowSharedKeyAccess: false" in (INFRA / "modules" / "functions.bicep").read_text()
    assert "enableRbacAuthorization: true" in (INFRA / "modules" / "keyvault.bicep").read_text()
    assert "adminUserEnabled: false" in (INFRA / "modules" / "registry.bicep").read_text()
    ca = (INFRA / "modules" / "containerapp.bicep").read_text()
    assert (
        "type: 'azure-servicebus'" in ca and "identity: identityId" in ca
    )  # KEDA scaler uses the worker identity


def test_every_workload_has_its_own_identity_and_azd_service():
    src = _main()
    names = re.findall(r"\{ name: '([a-z0-9-]+)', reg: '", src)
    azure_yaml = (ROOT / "azure.yaml").read_text()
    for n in names:
        assert f"\n  {n}:\n" in azure_yaml, n
    assert "host: function" in azure_yaml and "bpm-functions" in azure_yaml
    assert (
        "identityNames = concat(map(httpApps, a => a.name), map(workers, w => w.name), ['bpm-functions'])"
        in src
    )


def test_workload_registrations_exist_in_code():
    from aiip.identity.registrations import REGISTRATIONS

    regs = set(re.findall(r"reg: '([a-z0-9-]+)'", _main()))
    assert regs <= set(REGISTRATIONS), regs - set(REGISTRATIONS)


def test_workload_commands_point_at_real_modules():
    import importlib

    for mod in set(re.findall(r"'(aiip\.[a-z_.]+):app'", _main())):
        assert hasattr(importlib.import_module(mod), "app"), mod
    for mod in set(re.findall(r"'-m', '(aiip\.[a-z_.]+)'", _main())):
        importlib.import_module(mod)


def test_parameters_file_maps_azd_env():
    p = json.loads((INFRA / "main.parameters.json").read_text())["parameters"]
    assert p["environmentName"]["value"] == "${AZURE_ENV_NAME}"
    assert p["deployFrontDoor"]["value"].endswith("=false}")


def test_deploy_workflow_is_gated_oidc_and_promotes_with_approval():
    wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert "workflow_dispatch" in wf and "deploy_tool" in wf and "bicep" in wf
    # every job but the gate report is skipped unless the repo variable is set
    assert wf.count("if: vars.DEPLOY_ENABLED == 'true'") >= 2
    assert "id-token: write" in wf and "environment: dev" in wf and "environment: prod" in wf
    assert "azure/login@" in wf  # pinned to a commit SHA, see test_workflows_are_hardened
    assert "client-secret" not in wf.lower() and "AZURE_CLIENT_SECRET" not in wf
    teardown = (ROOT / ".github" / "workflows" / "teardown.yml").read_text()
    assert "workflow_dispatch" in teardown and "push:" not in teardown
    assert "vars.DEPLOY_ENABLED == 'true'" in teardown


def test_ci_runs_tests_lint_gate_and_bicep():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    for step in (
        "ruff check",
        "ruff format --check",
        "pytest",
        "run_eval_gate.py",
        "export_contracts.py --check",
        "build_dashboards.py --check",
        "bicep build",
        "demo.py",
    ):
        assert step in ci, step


def test_cost_estimate_has_no_invented_prices():
    text = (ROOT / "docs" / "cost-estimate.md").read_text()
    assert not re.search(r"\$\s?\d", text), "no dollar figures: link official pricing instead"
    assert "azure.microsoft.com/pricing" in text


@pytest.mark.skipif(
    os.environ.get("CI") != "true", reason="docker build only checked in CI when docker exists"
)
def test_dockerfile_uses_non_root_user():
    assert "USER 10001" in (ROOT / "Dockerfile").read_text()


def test_workflows_are_hardened():
    """Supply-chain guard: every third-party action is pinned to a full commit SHA with a version
    comment, every workflow sets top-level permissions, CI runs gitleaks, and CodeQL and Dependabot
    are configured. Dependabot bumps keep the SHA and the comment together, so this stays green."""
    wf_dir = ROOT / ".github" / "workflows"
    for f in sorted(wf_dir.glob("*.yml")):
        text = f.read_text()
        assert re.search(r"^permissions:", text, re.M), f"{f.name}: no top-level permissions"
        for line in text.splitlines():
            m = re.search(r"\buses:\s*([^\s#]+)\s*(#.*)?$", line)
            if m and not m.group(1).startswith("./"):
                assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", m.group(1)), f"{f.name}: {line.strip()}"
                assert m.group(2) and re.match(r"#\s*v\d", m.group(2)), f"{f.name}: no version comment"
    assert "gitleaks/gitleaks-action@" in (wf_dir / "ci.yml").read_text()
    assert "github/codeql-action/analyze@" in (wf_dir / "codeql.yml").read_text()
    deps = (ROOT / ".github" / "dependabot.yml").read_text()
    assert "package-ecosystem: github-actions" in deps and "interval: weekly" in deps
