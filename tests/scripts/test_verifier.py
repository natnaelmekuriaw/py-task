import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELLS = [shell for shell in ("pwsh", "powershell") if shutil.which(shell)]
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is unavailable")


def run_shell(
    shell: str, code: str, **environment: str
) -> subprocess.CompletedProcess[str]:
    bootstrap = (
        "$ProgressPreference = 'SilentlyContinue'\n"
        ". (Join-Path $env:VERIFY_REPO 'scripts/verify.ps1')\n"
    )
    encoded = base64.b64encode((bootstrap + code).encode("utf-16le")).decode("ascii")
    return subprocess.run(
        [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-EncodedCommand",
            encoded,
        ],
        cwd=ROOT,
        env={**os.environ, "VERIFY_REPO": str(ROOT), **environment},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("shell", SHELLS)
def test_failed_native_command_stops_the_runner(shell: str) -> None:
    result = run_shell(
        shell,
        """
try {
    $arguments = @('-c', 'import sys; sys.exit(7)')
    Invoke-VerifyCommand 'Native check' $env:VERIFY_PYTHON $arguments
    exit 9
} catch {
    if ($_.Exception.Message -ne 'Native check failed (exit 7).') { exit 8 }
}
exit 0
""",
        VERIFY_PYTHON=sys.executable,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_successful_commands_can_write_progress_to_stderr(shell: str) -> None:
    result = run_shell(
        shell,
        """
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$arguments = @('-c', 'import sys; sys.stderr.write(chr(119))')
Invoke-VerifyCommand 'Progress check' $env:VERIFY_PYTHON $arguments
exit 0
""",
        VERIFY_PYTHON=sys.executable,
    )
    assert result.returncode == 0, result.stdout + result.stderr


MOCK_FLOW = r"""
$ErrorActionPreference = 'Stop'
$script:calls = [Collections.Generic.List[string]]::new()
$script:requests = [Collections.Generic.List[string]]::new()
$script:benchmarkArguments = @()
$script:benchmarkDatabase = $null
$originalLocation = (Get-Location).Path
$env:PYTASK_DATABASE_URL = 'previous-url'
$env:PYTASK_DATABASE_URL_FILE = 'previous-file'
$env:PYTASK_API_KEYS_SECRET_FILE = 'previous-manifest'
$env:BUILDKIT_PROGRESS = 'tty'
$env:COMPOSE_MENU = 'true'

function docker { throw 'Real Docker must never run in this test' }
function Push-Location {
    param($Path)
    Microsoft.PowerShell.Management\Push-Location -LiteralPath $env:VERIFY_WORKSPACE
}
function Invoke-VerifyCommand {
    param($Name, $FilePath, $Arguments, [switch]$CaptureOutput)
    $script:calls.Add($Name)
    if ($FilePath -eq 'docker' -and $Arguments[0] -eq 'compose') {
        $progressIndex = [Array]::IndexOf($Arguments, '--progress')
        $ansiIndex = [Array]::IndexOf($Arguments, '--ansi')
        if ($progressIndex -lt 0 -or $Arguments[$progressIndex + 1] -ne 'plain' -or
            $ansiIndex -lt 0 -or $Arguments[$ansiIndex + 1] -ne 'never') {
            throw 'Compose output still requires an interactive console'
        }
        if ($Arguments -contains 'run' -and $Arguments -notcontains '-T') {
            throw 'One-off containers still allocate a terminal'
        }
    }
    if ($Name -eq 'Build application image' -and
        ($env:BUILDKIT_PROGRESS -ne 'plain' -or $env:COMPOSE_MENU -ne 'false')) {
        throw 'Build output is not configured for automation'
    }
    if ($Name -eq $env:VERIFY_FAIL_STAGE) { throw 'synthetic failure' }
    if ($Name -eq 'Check Docker Desktop') { return 'linux' }
    if ($Name -eq 'Read Compose configuration') {
        return @'
{"services":{
    "api":{"ports":[{"target":8000,"published":"8087"}]},
    "postgres":{
        "ports":[{"target":5432,"published":"18543"}],
        "environment":{
            "POSTGRES_USER":"local", "POSTGRES_PASSWORD":"p@ss",
            "POSTGRES_DB":"queue"
        }
    }
}}
'@
    }
    if ($Name -eq 'Generate local verification key') {
        $manifest = $Arguments[[Array]::IndexOf($Arguments, '--manifest') + 1]
        $token = $Arguments[[Array]::IndexOf($Arguments, '--token-file') + 1]
        Set-Content -LiteralPath $manifest -Value 'synthetic-manifest'
        Set-Content -LiteralPath $token -Value 'synthetic-token'
    }
    if ($Name -eq 'Run the full test suite') {
        $script:testDatabase = $env:PYTASK_DATABASE_URL
        if (Test-Path Env:PYTASK_DATABASE_URL_FILE) {
            throw 'conflicting database source'
        }
    }
    if ($Name -eq 'Run task engine benchmarks') {
        $script:benchmarkArguments = @($Arguments)
        $script:benchmarkDatabase = $env:PYTASK_DATABASE_URL
    }
}
function Invoke-VerifyRequest {
    param($Client, $Uri, $Token, $Body)
    $script:requests.Add($Uri)
    if ($Uri.EndsWith('/health/ready')) {
        return [pscustomobject]@{StatusCode=200; Json=@{status='ok'}}
    }
    if ($Uri.EndsWith('/tasks') -and -not $Body) {
        $challenge = 'Bearer realm="pytask"'
        if ($env:VERIFY_BAD_CHALLENGE) { $challenge = '' }
        return [pscustomobject]@{StatusCode=401; Challenge=$challenge}
    }
    if ($Body) {
        return [pscustomobject]@{
            StatusCode=201
            Json=@{id='09af5ac6-747d-4778-a38e-a64d44593d03'}
        }
    }
    if ($Uri.EndsWith('/monitoring')) { return [pscustomobject]@{StatusCode=200} }
    $status = if ($env:VERIFY_PENDING_TASK) { 'pending' } else { 'succeeded' }
    return [pscustomobject]@{StatusCode=200; Json=@{status=$status; result=3}}
}

$failure = $null
try {
    $options = @{
        ManifestFile='.secrets/api-keys.json'
        TokenFile='.secrets/local-token.txt'
        TaskTimeoutSeconds=0
    }
    if ($env:VERIFY_BENCHMARKS) {
        $options.Benchmarks = $true
        $options.BenchmarkTasks = 7
        $options.BenchmarkWorkers = @(2, 1)
        $options.BenchmarkRepeat = 2
        $options.BenchmarkWarmup = 1
        $options.BenchmarkTimeoutSeconds = 90
        $options.BenchmarkOutput = 'reports with spaces'
    }
    switch ($env:VERIFY_BAD_BENCHMARK) {
        'workers' {
            $options.Benchmarks = $true
            $options.BenchmarkWorkers = @(1, 1)
        }
        'budget' {
            $options.Benchmarks = $true
            $options.BenchmarkTasks = 10000
            $options.BenchmarkRepeat = 10
        }
        'output' {
            $options.Benchmarks = $true
            $options.BenchmarkOutput = ' '
        }
    }
    Invoke-LocalVerification @options
} catch { $failure = $_.Exception.Message }
[pscustomobject]@{
    failure=$failure
    calls=@($script:calls)
    requests=@($script:requests)
    database=if (Get-Variable testDatabase -Scope Script -ErrorAction Ignore) {
        $script:testDatabase
    } else { $null }
    benchmarkArguments=@($script:benchmarkArguments)
    benchmarkDatabase=$script:benchmarkDatabase
    restored=(
        $env:PYTASK_DATABASE_URL -eq 'previous-url' -and
        $env:PYTASK_DATABASE_URL_FILE -eq 'previous-file' -and
        $env:PYTASK_API_KEYS_SECRET_FILE -eq 'previous-manifest' -and
        $env:BUILDKIT_PROGRESS -eq 'tty' -and
        $env:COMPOSE_MENU -eq 'true' -and
        (Get-Location).Path -eq $originalLocation
    )
} | ConvertTo-Json -Depth 5 -Compress
"""


def workspace(tmp_path: Path, *, manifest: bool = True, token: bool = True) -> Path:
    (tmp_path / ".venv/Scripts").mkdir(parents=True)
    (tmp_path / ".venv/Scripts/python.exe").touch()
    (tmp_path / ".secrets").mkdir()
    (tmp_path / "examples/api").mkdir(parents=True)
    (tmp_path / "examples/api/task.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".env").write_text("existing-settings", encoding="utf-8")
    if manifest:
        (tmp_path / ".secrets/api-keys.json").write_text(
            "existing-manifest", encoding="utf-8"
        )
    if token:
        (tmp_path / ".secrets/local-token.txt").write_text(
            "existing-token", encoding="utf-8"
        )
    return tmp_path


def run_flow(shell: str, tmp_path: Path, **environment: str) -> dict[str, object]:
    result = run_shell(shell, MOCK_FLOW, VERIFY_WORKSPACE=str(tmp_path), **environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "existing-token" not in result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("shell", SHELLS)
def test_complete_flow_preserves_keys_and_uses_compose_ports(
    shell: str,
    tmp_path: Path,
) -> None:
    state = run_flow(shell, workspace(tmp_path))
    assert state["failure"] is None
    assert state["restored"] is True
    assert "Generate local verification key" not in state["calls"]
    assert (
        state["database"] == "postgresql+psycopg://local:p%40ss@127.0.0.1:18543/queue"
    )
    assert all(uri.startswith("http://127.0.0.1:8087/") for uri in state["requests"])
    calls = state["calls"]
    assert calls.index("Apply database migrations") < calls.index(
        "Run the full test suite"
    )
    assert calls.index("Run the full test suite") < calls.index(
        "Start and wait for application services"
    )
    assert (tmp_path / ".secrets/local-token.txt").read_text() == "existing-token"
    assert (tmp_path / ".env").read_text() == "existing-settings"
    assert "Run task engine benchmarks" not in calls


@pytest.mark.parametrize("shell", SHELLS)
def test_benchmarks_run_after_tests_and_before_runtime_startup(shell, tmp_path):
    state = run_flow(shell, workspace(tmp_path), VERIFY_BENCHMARKS="1")
    assert state["failure"] is None
    calls = state["calls"]
    assert (
        calls.index("Run the full test suite")
        < calls.index("Run task engine benchmarks")
        < calls.index("Start and wait for application services")
    )
    assert state["benchmarkDatabase"] == state["database"]
    assert state["benchmarkArguments"] == [
        "-m",
        "pytask_service.benchmarks",
        "--tasks",
        "7",
        "--workers",
        "2",
        "1",
        "--repeat",
        "2",
        "--warmup",
        "1",
        "--case-timeout",
        "90",
        "--output",
        "reports with spaces",
    ]
    assert state["restored"] is True


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "invalid,reason",
    [
        ("workers", "distinct counts"),
        ("budget", "100000"),
        ("output", "cannot be blank"),
    ],
)
def test_invalid_benchmark_plan_stops_before_any_commands(
    shell, tmp_path, invalid, reason
):
    state = run_flow(shell, workspace(tmp_path), VERIFY_BAD_BENCHMARK=invalid)
    assert reason in state["failure"]
    assert not state["calls"]
    assert not state["requests"]
    assert state["restored"] is True


@pytest.mark.parametrize("shell", SHELLS)
def test_failure_does_not_start_services_and_restores_the_environment(
    shell: str,
    tmp_path: Path,
) -> None:
    state = run_flow(
        shell, workspace(tmp_path), VERIFY_FAIL_STAGE="Run the full test suite"
    )
    assert state["failure"] == "synthetic failure"
    assert state["restored"] is True
    assert "Start and wait for application services" not in state["calls"]
    assert state["requests"] == []


@pytest.mark.parametrize("shell", SHELLS)
def test_first_run_generates_keys_only_when_both_are_missing(
    shell: str, tmp_path: Path
) -> None:
    state = run_flow(shell, workspace(tmp_path, manifest=False, token=False))
    assert state["failure"] is None
    assert "Generate local verification key" in state["calls"]
    assert state["restored"] is True


@pytest.mark.parametrize("shell", SHELLS)
def test_partial_key_setup_is_preserved_and_rejected(
    shell: str, tmp_path: Path
) -> None:
    state = run_flow(shell, workspace(tmp_path, token=False))
    assert "Only one key file exists" in state["failure"]
    assert "Generate local verification key" not in state["calls"]
    assert state["restored"] is True
    assert not (tmp_path / ".secrets/local-token.txt").exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_failed_benchmarks_stop_runtime_startup_and_restore_environment(
    shell, tmp_path
):
    state = run_flow(
        shell,
        workspace(tmp_path),
        VERIFY_BENCHMARKS="1",
        VERIFY_FAIL_STAGE="Run task engine benchmarks",
    )
    assert state["failure"] == "synthetic failure"
    assert "Run the full test suite" in state["calls"]
    assert "Start and wait for application services" not in state["calls"]
    assert not state["requests"]
    assert state["restored"] is True


@pytest.mark.parametrize("shell", SHELLS)
def test_anonymous_access_must_include_a_bearer_challenge(
    shell: str, tmp_path: Path
) -> None:
    state = run_flow(shell, workspace(tmp_path), VERIFY_BAD_CHALLENGE="1")
    assert "401 Bearer challenge" in state["failure"]
    assert len(state["requests"]) == 2
    assert state["restored"] is True


@pytest.mark.parametrize("shell", SHELLS)
def test_waiting_for_a_task_is_bounded(shell: str, tmp_path: Path) -> None:
    state = run_flow(shell, workspace(tmp_path), VERIFY_PENDING_TASK="1")
    assert "Timed out waiting" in state["failure"]
    assert not any(uri.endswith("/monitoring") for uri in state["requests"])
    assert state["restored"] is True


@pytest.mark.parametrize("shell", SHELLS)
def test_http_checks_use_headers_and_parse_challenges_without_a_network(
    shell: str,
) -> None:
    result = run_shell(
        shell,
        r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Net.Http
$references = @(
    [System.Net.Http.HttpClient].Assembly.Location,
    [System.Net.HttpStatusCode].Assembly.Location
)
Add-Type -ReferencedAssemblies $references -TypeDefinition @'
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
public class VerifyMemoryHandler : HttpMessageHandler {
    public string Authorization;
    public string Method;
    protected override Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request, CancellationToken token
    ) {
        Authorization = request.Headers.Authorization == null
            ? null : request.Headers.Authorization.ToString();
        Method = request.Method.Method;
        var response = new HttpResponseMessage(HttpStatusCode.Unauthorized);
        response.Content = new StringContent(
            "{\"error\":{\"code\":\"authentication_required\"}}"
        );
        response.Headers.WwwAuthenticate.ParseAdd("Bearer realm=\"pytask\"");
        return Task.FromResult(response);
    }
}
'@
$handler = [VerifyMemoryHandler]::new()
$client = [System.Net.Http.HttpClient]::new($handler)
try {
    $anonymous = Invoke-VerifyRequest $client 'http://127.0.0.1/unused'
    if ($anonymous.StatusCode -ne 401 -or
        $anonymous.Challenge -notmatch '^Bearer\b') { exit 2 }
    if ($null -ne $handler.Authorization -or $handler.Method -ne 'GET') { exit 3 }
    $parameters = @{
        Client=$client
        Uri='http://127.0.0.1/unused'
        Token='synthetic-private-token'
        Body='{}'
    }
    $authorized = Invoke-VerifyRequest @parameters
    if ($handler.Authorization -ne 'Bearer synthetic-private-token' -or
        $handler.Method -ne 'POST') { exit 4 }
    if ($authorized.Json.error.code -ne 'authentication_required') { exit 5 }
} finally { $client.Dispose() }
exit 0
""",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "synthetic-private-token" not in result.stdout + result.stderr
