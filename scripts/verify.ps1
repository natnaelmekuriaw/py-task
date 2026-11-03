#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$DatabaseUrl = $env:PYTASK_DATABASE_URL,
    [string]$ManifestFile = '.secrets/api-keys.json',
    [string]$TokenFile = '.secrets/local-token.txt',
    [ValidateRange(10, 600)]
    [int]$StartupTimeoutSeconds = 120,
    [ValidateRange(5, 600)]
    [int]$TaskTimeoutSeconds = 60,
    [switch]$SkipInstall,
    [switch]$SkipBuild,
    [switch]$Benchmarks,
    [ValidateRange(1, 10000)]
    [int]$BenchmarkTasks = 20,
    [ValidateRange(1, 32)]
    [int[]]$BenchmarkWorkers = @(1, 2, 4),
    [ValidateRange(1, 10)]
    [int]$BenchmarkRepeat = 3,
    [ValidateRange(0, 1000)]
    [int]$BenchmarkWarmup = 2,
    [ValidateRange(5, 3600)]
    [int]$BenchmarkTimeoutSeconds = 120,
    [string]$BenchmarkOutput = 'benchmark-results'
)

function Invoke-VerifyCommand {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [switch]$CaptureOutput
    )

    Write-Host "`n==> $Name" -ForegroundColor Cyan
    $global:LASTEXITCODE = 0
    if ($CaptureOutput) {
        $output = @(& $FilePath @Arguments)
    }
    else {
        & $FilePath @Arguments
    }
    if ($global:LASTEXITCODE -ne 0) {
        throw "$Name failed (exit $global:LASTEXITCODE)."
    }
    if ($CaptureOutput) {
        return ($output -join [Environment]::NewLine)
    }
}

function Get-VerifyPort {
    param([object]$Service, [int]$Target)

    $bindings = @($Service.ports | Where-Object { $_.target -eq $Target })
    $port = 0
    if ($bindings.Count -ne 1 -or
        -not [int]::TryParse([string]$bindings[0].published, [ref]$port) -or
        $port -lt 1 -or $port -gt 65535) {
        throw "Compose must publish one host port for container port $Target."
    }
    return $port
}

function Invoke-VerifyRequest {
    param(
        [System.Net.Http.HttpClient]$Client,
        [string]$Uri,
        [string]$Token,
        [string]$Body
    )

    $method = if ($Body) { 'POST' } else { 'GET' }
    $request = [System.Net.Http.HttpRequestMessage]::new(
        [System.Net.Http.HttpMethod]::new($method), $Uri
    )
    $response = $null
    try {
        if ($Token) {
            $request.Headers.Authorization =
                [System.Net.Http.Headers.AuthenticationHeaderValue]::new('Bearer', $Token)
        }
        if ($Body) {
            $request.Content = [System.Net.Http.StringContent]::new(
                $Body, [Text.Encoding]::UTF8, 'application/json'
            )
        }
        $response = $Client.SendAsync($request).GetAwaiter().GetResult()
        $content = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        return [pscustomobject]@{
            StatusCode = [int]$response.StatusCode
            Challenge = $response.Headers.WwwAuthenticate.ToString()
            Json = ConvertFrom-Json -InputObject $content -ErrorAction Stop
        }
    }
    catch {
        throw 'API request failed or returned invalid JSON; check service availability.'
    }
    finally {
        if ($null -ne $response) { $response.Dispose() }
        $request.Dispose()
    }
}

function Invoke-LocalVerification {
    param(
        [string]$DatabaseUrl,
        [string]$ManifestFile,
        [string]$TokenFile,
        [int]$StartupTimeoutSeconds = 120,
        [int]$TaskTimeoutSeconds = 60,
        [switch]$SkipInstall,
        [switch]$SkipBuild,
        [switch]$Benchmarks,
        [int]$BenchmarkTasks = 20,
        [int[]]$BenchmarkWorkers = @(1, 2, 4),
        [int]$BenchmarkRepeat = 3,
        [int]$BenchmarkWarmup = 2,
        [int]$BenchmarkTimeoutSeconds = 120,
        [string]$BenchmarkOutput = 'benchmark-results'
    )

    Set-StrictMode -Version Latest
    $ErrorActionPreference = 'Stop'
    $PSNativeCommandUseErrorActionPreference = $false
    if ($Benchmarks) {
        if ($null -eq $BenchmarkWorkers -or $BenchmarkWorkers.Count -lt 1 -or
            $BenchmarkWorkers.Count -gt 8 -or
            @($BenchmarkWorkers | Select-Object -Unique).Count -ne $BenchmarkWorkers.Count -or
            @($BenchmarkWorkers | Where-Object { $_ -lt 1 -or $_ -gt 32 }).Count -gt 0) {
            throw 'Benchmark workers must contain 1 to 8 distinct counts from 1 to 32.'
        }
        if ($BenchmarkTasks -lt 1 -or $BenchmarkTasks -gt 10000 -or
            $BenchmarkRepeat -lt 1 -or $BenchmarkRepeat -gt 10 -or
            $BenchmarkWarmup -lt 0 -or $BenchmarkWarmup -gt 1000 -or
            $BenchmarkTimeoutSeconds -lt 5 -or $BenchmarkTimeoutSeconds -gt 3600) {
            throw 'Benchmark settings are outside the supported ranges.'
        }
        $plannedTasks = ([long]$BenchmarkTasks + $BenchmarkWarmup) *
            $BenchmarkRepeat * $BenchmarkWorkers.Count * 3
        if ($plannedTasks -gt 100000) {
            throw 'Benchmark plan exceeds 100000 total tasks including warmup.'
        }
        if ([string]::IsNullOrWhiteSpace($BenchmarkOutput)) {
            throw 'Benchmark output directory cannot be blank.'
        }
    }
    $savedEnvironment = @{}
    foreach ($name in @(
        'PYTASK_DATABASE_URL', 'PYTASK_DATABASE_URL_FILE', 'PYTASK_API_KEYS_SECRET_FILE',
        'BUILDKIT_PROGRESS', 'COMPOSE_MENU'
    )) {
        $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    $client = $null
    $started = [Diagnostics.Stopwatch]::StartNew()
    Push-Location (Split-Path -Parent $PSScriptRoot)
    try {
        $env:BUILDKIT_PROGRESS = 'plain'
        $env:COMPOSE_MENU = 'false'
        $composeOutput = @('compose', '--ansi', 'never', '--progress', 'plain')
        $python = Join-Path $PWD '.venv/Scripts/python.exe'
        if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
            throw 'Create the project .venv with Python 3.12 or newer first.'
        }
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
            throw 'Docker is unavailable. Install Docker Desktop and start Linux containers.'
        }
        $operatingSystem = Invoke-VerifyCommand 'Check Docker Desktop' docker @(
            'info', '--format', '{{.OSType}}'
        ) -CaptureOutput
        if ($operatingSystem.Trim() -ne 'linux') {
            throw 'Docker must be running with Linux containers.'
        }
        if (-not (Test-Path -LiteralPath '.env')) {
            Copy-Item -LiteralPath '.env.example' -Destination '.env'
        }
        Invoke-VerifyCommand 'Stop application services' docker (
            $composeOutput + @('--profile', 'runtime', 'stop', 'api', 'worker', 'scheduler')
        )
        if (-not $SkipInstall) {
            Invoke-VerifyCommand 'Install project dependencies' $python @(
                '-m', 'pip', 'install', '-c', 'constraints/runtime.txt', '-e', '.[dev]'
            )
        }

        $manifestPath = if ([IO.Path]::IsPathRooted($ManifestFile)) {
            [IO.Path]::GetFullPath($ManifestFile)
        } else { [IO.Path]::GetFullPath((Join-Path $PWD $ManifestFile)) }
        $tokenPath = if ([IO.Path]::IsPathRooted($TokenFile)) {
            [IO.Path]::GetFullPath($TokenFile)
        } else { [IO.Path]::GetFullPath((Join-Path $PWD $TokenFile)) }
        $hasManifest = Test-Path -LiteralPath $manifestPath -PathType Leaf
        $hasToken = Test-Path -LiteralPath $tokenPath -PathType Leaf
        if ($hasManifest -ne $hasToken) {
            throw 'Only one key file exists. Supply a matching manifest and token; existing files are preserved.'
        }
        if (-not $hasManifest) {
            foreach ($path in @($manifestPath, $tokenPath)) {
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
            }
            Invoke-VerifyCommand 'Generate local verification key' $python @(
                '-m', 'pytask_service.auth', 'generate', '--id', 'local-demo',
                '--scope', 'tasks:read', '--scope', 'tasks:submit',
                '--scope', 'schedules:read', '--scope', 'schedules:write',
                '--scope', 'monitoring:read', '--scope', 'docs:read',
                '--manifest', $manifestPath, '--token-file', $tokenPath
            )
        }
        $verifyKey = @'
import sys
from pathlib import Path
from pytask_service.auth.files import read_manifest_file
from pytask_service.auth.permissions import Permission
from pytask_service.auth.store import KeyStore
try:
    with Path(sys.argv[2]).open('rb') as stream:
        token = stream.read(129).decode('utf-8-sig').strip()
    principal = KeyStore(read_manifest_file(Path(sys.argv[1]))).authenticate(token)
    required = {Permission.TASKS_READ, Permission.TASKS_SUBMIT, Permission.MONITORING_READ}
    if not principal.permits(required):
        raise ValueError()
except Exception:
    sys.exit('Verification key files must match and grant tasks:read, tasks:submit, monitoring:read.')
print('Verification key is valid')
'@
        Invoke-VerifyCommand 'Validate existing key files' $python @(
            '-c', $verifyKey, $manifestPath, $tokenPath
        )
        $env:PYTASK_API_KEYS_SECRET_FILE = $manifestPath
        $compose = $composeOutput + @('-f', 'compose.yaml', '-f', 'compose.auth.yaml')
        $configuration = Invoke-VerifyCommand 'Read Compose configuration' docker (
            $compose + @('--profile', 'runtime', '--profile', 'tools', 'config', '--format', 'json')
        ) -CaptureOutput
        try {
            $services = (ConvertFrom-Json -InputObject $configuration).services
            $apiPort = Get-VerifyPort $services.api 8000
            if (-not $DatabaseUrl) {
                $postgres = $services.postgres
                $port = Get-VerifyPort $postgres 5432
                $userName = [Uri]::EscapeDataString($postgres.environment.POSTGRES_USER)
                $password = [Uri]::EscapeDataString($postgres.environment.POSTGRES_PASSWORD)
                $database = [Uri]::EscapeDataString($postgres.environment.POSTGRES_DB)
                $DatabaseUrl = "postgresql+psycopg://${userName}:${password}@127.0.0.1:${port}/$database"
            }
        }
        catch {
            throw 'Cannot determine local ports or database credentials from Compose configuration.'
        }
        $env:PYTASK_DATABASE_URL = $DatabaseUrl
        Remove-Item Env:PYTASK_DATABASE_URL_FILE -ErrorAction SilentlyContinue
        if (-not $SkipBuild) {
            Invoke-VerifyCommand 'Build application image' docker (
                $compose + @('--profile', 'runtime', 'build', 'api')
            )
        }
        Invoke-VerifyCommand 'Start and wait for PostgreSQL' docker (
            $compose + @('up', '-d', '--wait', '--wait-timeout', "$StartupTimeoutSeconds", 'postgres')
        )
        Invoke-VerifyCommand 'Check database connection' $python @(
            '-m', 'pytask_service.cli', 'check-db'
        )
        Invoke-VerifyCommand 'Apply database migrations' docker (
            $compose + @('--profile', 'tools', 'run', '-T', '--rm', 'migrate')
        )
        Invoke-VerifyCommand 'Check database schema' docker (
            $compose + @('--profile', 'tools', 'run', '-T', '--rm', 'ops', 'check')
        )
        Invoke-VerifyCommand 'Check migration drift' docker (
            $compose + @('--profile', 'tools', 'run', '-T', '--rm', '--entrypoint', 'python',
                'migrate', '-m', 'alembic', '-c', '/app/alembic.ini', 'check')
        )
        Invoke-VerifyCommand 'Lint Python' $python @('-m', 'ruff', 'check', '.')
        Invoke-VerifyCommand 'Check formatting' $python @('-m', 'ruff', 'format', '--check', '.')
        Invoke-VerifyCommand 'Check types' $python @('-m', 'mypy')
        Invoke-VerifyCommand 'Run the full test suite' $python @(
            '-m', 'pytest', '-x', '-vv', '--durations=10'
        )
        if ($Benchmarks) {
            $workerCounts = @($BenchmarkWorkers | ForEach-Object { [string]$_ })
            $benchmarkArguments = @(
                '-m', 'pytask_service.benchmarks', '--tasks', "$BenchmarkTasks", '--workers'
            ) + $workerCounts + @(
                '--repeat', "$BenchmarkRepeat", '--warmup', "$BenchmarkWarmup",
                '--case-timeout', "$BenchmarkTimeoutSeconds", '--output', $BenchmarkOutput
            )
            Invoke-VerifyCommand 'Run task engine benchmarks' $python $benchmarkArguments
        }
        Invoke-VerifyCommand 'Start and wait for application services' docker (
            $compose + @('--profile', 'runtime', 'up', '-d', '--no-build',
                '--wait', '--wait-timeout', "$StartupTimeoutSeconds")
        )

        Write-Host "`n==> Verify API access and task execution" -ForegroundColor Cyan
        Add-Type -AssemblyName System.Net.Http
        $handler = [System.Net.Http.HttpClientHandler]::new()
        $handler.UseProxy = $false
        $handler.AllowAutoRedirect = $false
        $client = [System.Net.Http.HttpClient]::new($handler)
        $client.Timeout = [TimeSpan]::FromSeconds(10)
        $baseUri = "http://127.0.0.1:$apiPort"
        $health = Invoke-VerifyRequest $client "$baseUri/health/ready"
        if ($health.StatusCode -ne 200 -or $health.Json.status -ne 'ok') {
            throw 'API readiness did not return status ok.'
        }
        $anonymous = Invoke-VerifyRequest $client "$baseUri/api/v1/tasks"
        if ($anonymous.StatusCode -ne 401 -or $anonymous.Challenge -notmatch '^Bearer\b') {
            throw 'Anonymous API access did not return a 401 Bearer challenge.'
        }
        $token = (Get-Content -Raw -LiteralPath $tokenPath).Trim()
        $body = Get-Content -Raw -LiteralPath 'examples/api/task.json'
        $submitted = Invoke-VerifyRequest $client "$baseUri/api/v1/tasks" $token $body
        $taskId = [guid]::Empty
        if ($submitted.StatusCode -ne 201 -or
            -not [guid]::TryParse([string]$submitted.Json.id, [ref]$taskId)) {
            throw 'Authenticated example submission failed.'
        }
        $waiting = [Diagnostics.Stopwatch]::StartNew()
        do {
            $task = Invoke-VerifyRequest $client "$baseUri/api/v1/tasks/$taskId" $token
            if ($task.StatusCode -ne 200) { throw 'Cannot read the submitted example task.' }
            if ($task.Json.status -eq 'succeeded') {
                if ($task.Json.result -ne 3) { throw 'Example task returned an unexpected result.' }
                break
            }
            if ($task.Json.status -notin @('pending', 'running')) {
                throw 'Example task failed or entered an unexpected state.'
            }
            if ($waiting.Elapsed.TotalSeconds -ge $TaskTimeoutSeconds) {
                throw 'Timed out waiting for the example task; check worker logs.'
            }
            Start-Sleep -Seconds 1
        } while ($true)
        $monitoring = Invoke-VerifyRequest $client "$baseUri/api/v1/monitoring" $token
        if ($monitoring.StatusCode -ne 200) { throw 'Authenticated monitoring request failed.' }
        Write-Host "`nAll checks passed in $([int]$started.Elapsed.TotalSeconds) seconds. Containers remain running." -ForegroundColor Green
    }
    finally {
        if ($null -ne $client) { $client.Dispose() }
        foreach ($name in $savedEnvironment.Keys) {
            [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
        }
        Pop-Location
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $options = @{
            DatabaseUrl = $DatabaseUrl
            ManifestFile = $ManifestFile
            TokenFile = $TokenFile
            StartupTimeoutSeconds = $StartupTimeoutSeconds
            TaskTimeoutSeconds = $TaskTimeoutSeconds
            SkipInstall = $SkipInstall
            SkipBuild = $SkipBuild
            Benchmarks = $Benchmarks
            BenchmarkTasks = $BenchmarkTasks
            BenchmarkWorkers = $BenchmarkWorkers
            BenchmarkRepeat = $BenchmarkRepeat
            BenchmarkWarmup = $BenchmarkWarmup
            BenchmarkTimeoutSeconds = $BenchmarkTimeoutSeconds
            BenchmarkOutput = $BenchmarkOutput
        }
        Invoke-LocalVerification @options
        exit 0
    }
    catch {
        Write-Host "`nVerification stopped: $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}
