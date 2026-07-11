param(
    [string]$CertsDir = "certs",
    [string]$MiloHost = "localhost",
    [int]$MiloPort = 50056,
    [string]$JudyHost = "host.docker.internal",
    [int]$JudyPort = 50052,
    [switch]$SkipHandshake
)

$ErrorActionPreference = "Stop"

function Invoke-External {
    param(
        [string]$Command,
        [string[]]$Arguments,
        [string]$FailureMessage
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw $FailureMessage
    }
}

function Test-RequiredFile {
    param([string]$Path)

    if (-not (Test-Path -Path $Path -PathType Leaf)) {
        throw "Missing required file: $Path"
    }
}

if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
    throw "OpenSSL is required. Install OpenSSL and ensure 'openssl' is on PATH."
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$certsRoot = Join-Path $repoRoot $CertsDir

$clientsCaCert = Join-Path $certsRoot "ca/clients-ca.crt"
$judyCaCert = Join-Path $certsRoot "ca/judy-ca.crt"
$miloServerCaCert = Join-Path $certsRoot "ca/milo-server-ca.crt"

$miloServerCert = Join-Path $certsRoot "milo/server.crt"
$miloServerKey = Join-Path $certsRoot "milo/server.key"
$miloClientCert = Join-Path $certsRoot "milo/client.crt"
$miloClientKey = Join-Path $certsRoot "milo/client.key"

$judyServerCert = Join-Path $certsRoot "judy/server.crt"
$clientsCallerCert = Join-Path $certsRoot "clients/caller.crt"
$clientsCallerKey = Join-Path $certsRoot "clients/caller.key"

$requiredFiles = @(
    $clientsCaCert,
    $judyCaCert,
    $miloServerCaCert,
    $miloServerCert,
    $miloServerKey,
    $miloClientCert,
    $miloClientKey,
    $judyServerCert,
    $clientsCallerCert,
    $clientsCallerKey
)

foreach ($path in $requiredFiles) {
    Test-RequiredFile -Path $path
}

Write-Host "[1/4] Verifying certificate trust chains..."
Invoke-External -Command "openssl" -Arguments @("verify", "-CAfile", $miloServerCaCert, $miloServerCert) -FailureMessage "Milo server cert failed CA validation"
Invoke-External -Command "openssl" -Arguments @("verify", "-CAfile", $judyCaCert, $judyServerCert) -FailureMessage "Judy server cert failed CA validation"
Invoke-External -Command "openssl" -Arguments @("verify", "-CAfile", $judyCaCert, $miloClientCert) -FailureMessage "Milo client cert failed Judy CA validation"
Invoke-External -Command "openssl" -Arguments @("verify", "-CAfile", $clientsCaCert, $clientsCallerCert) -FailureMessage "Caller client cert failed Clients CA validation"

if ($SkipHandshake) {
    Write-Host "[2/4] Handshake checks skipped (--SkipHandshake)."
    Write-Host "mTLS certificate integrity checks passed."
    exit 0
}

Write-Host "[2/4] Checking Milo inbound mTLS handshake..."
$nullInput = [System.IO.Path]::GetTempFileName()
Set-Content -Path $nullInput -Value "" -Encoding ASCII
try {
    Invoke-External -Command "openssl" -Arguments @(
        "s_client",
        "-connect", "$MiloHost`:$MiloPort",
        "-CAfile", $miloServerCaCert,
        "-cert", $clientsCallerCert,
        "-key", $clientsCallerKey,
        "-verify_return_error",
        "-brief"
    ) -FailureMessage "Failed mTLS handshake to Milo at $MiloHost:$MiloPort"
}
finally {
    Remove-Item -Path $nullInput -Force -ErrorAction SilentlyContinue
}

Write-Host "[3/4] Checking Judy outbound mTLS handshake path from Milo identity..."
Invoke-External -Command "openssl" -Arguments @(
    "s_client",
    "-connect", "$JudyHost`:$JudyPort",
    "-CAfile", $judyCaCert,
    "-cert", $miloClientCert,
    "-key", $miloClientKey,
    "-verify_return_error",
    "-brief"
) -FailureMessage "Failed mTLS handshake to Judy at $JudyHost:$JudyPort"

Write-Host "[4/4] mTLS verification completed successfully."
