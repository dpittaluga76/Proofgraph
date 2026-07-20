[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$')]
    [string]$Hostname,

    [string]$SourceEnvPath = ".env",

    [string]$OutputPath = ".env.public",

    [switch]$CopyOpenAIKey,

    [switch]$Force
)

$ErrorActionPreference = "Stop"

function New-UrlSafeSecret {
    param([int]$ByteCount)

    $bytes = New-Object byte[] $ByteCount
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Read-EnvValues {
    param([string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path)) {
        return $values
    }
    Get-Content -LiteralPath $Path | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            $values[$matches[1]] = $matches[2].Trim()
        }
    }
    return $values
}

$resolvedOutput = [IO.Path]::GetFullPath((Join-Path (Get-Location) $OutputPath))
if ((Test-Path -LiteralPath $resolvedOutput) -and -not $Force) {
    throw "$OutputPath already exists. Pass -Force only when intentionally rotating its secrets."
}

$sourceValues = Read-EnvValues -Path $SourceEnvPath
$openAIKey = ""
if ($CopyOpenAIKey) {
    $openAIKey = $sourceValues["OPENAI_API_KEY"]
    if ([string]::IsNullOrWhiteSpace($openAIKey)) {
        throw "OPENAI_API_KEY is not set in $SourceEnvPath."
    }
}

$databasePassword = New-UrlSafeSecret -ByteCount 36
$djangoSecret = New-UrlSafeSecret -ByteCount 64
$lines = @(
    "POSTGRES_DB=proofgraph",
    "POSTGRES_USER=proofgraph",
    "POSTGRES_PASSWORD=$databasePassword",
    "DATABASE_URL=postgresql://proofgraph:$databasePassword@db:5432/proofgraph",
    "DATABASE_CONN_MAX_AGE=60",
    "",
    "DJANGO_DEBUG=false",
    "DJANGO_SECRET_KEY=$djangoSecret",
    "DJANGO_ALLOWED_HOSTS=$Hostname",
    "DJANGO_CSRF_TRUSTED_ORIGINS=https://$Hostname",
    "DEMO_PUBLIC_MODE=true",
    "",
    "OPENAI_API_KEY=$openAIKey"
)

[IO.File]::WriteAllLines($resolvedOutput, $lines, [Text.UTF8Encoding]::new($false))
Write-Output "Created $OutputPath for $Hostname. Secret values were not printed."
