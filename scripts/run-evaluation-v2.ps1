<#
.SYNOPSIS
Runs or resumes the fresh comparative-evaluation V2 workflow.

.DESCRIPTION
Orchestrates the existing Django generation, packet-preparation, automated-judge, and analysis
commands. Paid stages require -ConfirmCost. Use -DryRun to inspect every command without creating
artifacts or making provider calls. Frozen semantic settings are recorded in run-config-v2.json.

.EXAMPLE
.\scripts\run-evaluation-v2.ps1 -DryRun

.EXAMPLE
.\scripts\run-evaluation-v2.ps1 -ConfirmCost

.EXAMPLE
.\scripts\run-evaluation-v2.ps1 -Stage judge -Workers 3 -ConfirmCost
#>
[CmdletBinding()]
param(
    [ValidateSet("all", "generate", "prepare", "judge", "analyze")]
    [string]$Stage = "all",

    [string]$RunDirectory = "evaluation/runs/eval-terra-v2",
    [string]$Scenarios = "evaluation/scenarios.v1.json",

    [ValidateSet("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")]
    [string]$GenerationModel = "gpt-5.6-terra",

    [ValidateSet("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")]
    [string]$JudgeAModel = "gpt-5.6-sol",

    [ValidateSet("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")]
    [string]$JudgeBModel = "gpt-5.6-luna",

    [ValidateRange(1, 8)]
    [int]$Workers = 6,

    [int]$GenerationSeed = 28001,
    [int]$PacketSeed = 28002,
    [int]$JudgeSeed = 28003,

    [switch]$ConfirmCost,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RepoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$RunsRoot = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot "evaluation/runs"))
$V1RunPath = [System.IO.Path]::GetFullPath((Join-Path $RepoRoot "evaluation/runs/eval-terra-v1"))

function Resolve-RepoPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $RepoRoot $Path))
}

function Format-CommandArgument {
    param([Parameter(Mandatory = $true)][string]$Value)

    if ($Value -match '[\s"]') {
        return '"' + $Value.Replace('"', '\"') + '"'
    }
    return $Value
}

function Assert-Artifact {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$RequiredFor
    )

    if ($DryRun) {
        return
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Stage '$RequiredFor' requires the missing artifact: $Path"
    }
}

function Invoke-ManageCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$Paid
    )

    $EffectiveArguments = @($Name) + $Arguments
    if ($Paid) {
        $EffectiveArguments += "--confirm-cost"
    }
    $DisplayArguments = $EffectiveArguments | ForEach-Object {
        Format-CommandArgument -Value $_
    }
    Write-Host "`n> uv run python manage.py $($DisplayArguments -join ' ')"

    if ($DryRun) {
        return
    }

    & uv run python manage.py @EffectiveArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Management command '$Name' failed with exit code $LASTEXITCODE."
    }
}

$RunPath = Resolve-RepoPath -Path $RunDirectory
$ScenarioPath = Resolve-RepoPath -Path $Scenarios
$RunsPrefix = $RunsRoot + [System.IO.Path]::DirectorySeparatorChar

if (-not $RunPath.StartsWith($RunsPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "RunDirectory must be inside $RunsRoot so private artifacts remain ignored."
}
if ($RunPath.Equals($V1RunPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The frozen V1 directory is protected. Choose a distinct V2 RunDirectory."
}
if (-not (Test-Path -LiteralPath $ScenarioPath -PathType Leaf)) {
    throw "Scenario file not found: $ScenarioPath"
}

$SelectedStages = if ($Stage -eq "all") {
    @("generate", "prepare", "judge", "analyze")
}
else {
    @($Stage)
}
$HasPaidStage = $SelectedStages -contains "generate" -or $SelectedStages -contains "judge"
if ($HasPaidStage -and -not $ConfirmCost -and -not $DryRun) {
    throw (
        "Stage '$Stage' includes paid provider calls. Re-run with -ConfirmCost after reviewing " +
        "the run settings, or use -DryRun to inspect the commands without executing them."
    )
}

$ConfigPath = Join-Path $RunPath "run-config-v2.json"
$ExpectedConfig = [ordered]@{
    schema_version   = 1
    acceptance_rule = "comparative_acceptance_v2"
    scenario_path    = $ScenarioPath
    generation_model = $GenerationModel
    judge_a_model    = $JudgeAModel
    judge_b_model    = $JudgeBModel
    generation_seed  = $GenerationSeed
    packet_seed      = $PacketSeed
    judge_seed       = $JudgeSeed
}

if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
    $ExistingConfig = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
    $Mismatches = @()
    foreach ($Key in $ExpectedConfig.Keys) {
        $ExistingProperty = $ExistingConfig.PSObject.Properties[$Key]
        if ($null -eq $ExistingProperty) {
            $Mismatches += "$Key is missing"
        }
        elseif ([string]$ExistingProperty.Value -cne [string]$ExpectedConfig[$Key]) {
            $Mismatches += "$Key expected '$($ExpectedConfig[$Key])' but found '$($ExistingProperty.Value)'"
        }
    }
    if ($Mismatches.Count -gt 0) {
        throw (
            "Existing V2 run manifest does not match the requested frozen settings:`n- " +
            ($Mismatches -join "`n- ") +
            "`nPreserve the existing run and choose a new RunDirectory for changed settings."
        )
    }
}
else {
    $ExistingArtifacts = @(
        if (Test-Path -LiteralPath $RunPath -PathType Container) {
            Get-ChildItem -LiteralPath $RunPath -File -Recurse -Force
        }
    )
    if ($ExistingArtifacts.Count -gt 0) {
        throw (
            "Refusing to adopt existing artifacts without run-config-v2.json. " +
            "Preserve them and choose a new RunDirectory."
        )
    }

    if ($DryRun) {
        Write-Host "Dry run: would create frozen manifest $ConfigPath"
    }
    else {
        New-Item -ItemType Directory -Path $RunPath -Force | Out-Null
        $TemporaryConfigPath = "$ConfigPath.tmp-$PID"
        try {
            $ConfigJson = $ExpectedConfig | ConvertTo-Json
            $Utf8WithoutBom = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($TemporaryConfigPath, $ConfigJson + "`n", $Utf8WithoutBom)
            Move-Item -LiteralPath $TemporaryConfigPath -Destination $ConfigPath
        }
        finally {
            if (Test-Path -LiteralPath $TemporaryConfigPath) {
                Remove-Item -LiteralPath $TemporaryConfigPath -Force
            }
        }
        Write-Host "Created frozen V2 run manifest: $ConfigPath"
    }
}

if (-not $DryRun) {
    Get-Command uv -ErrorAction Stop | Out-Null
}

$GenerationPath = Join-Path $RunPath "private-generation.json"
$RatingPath = Join-Path $RunPath "rating"
$PacketPath = Join-Path $RatingPath "blind-packet.json"
$PrivateMapPath = Join-Path $RatingPath "private-variant-map.json"
$JudgeAPath = Join-Path $RatingPath "rating-judge-a.json"
$JudgeBPath = Join-Path $RatingPath "rating-judge-b.json"
$ResultJsonPath = Join-Path $RunPath "result.json"
$ResultMarkdownPath = Join-Path $RunPath "result.md"

Push-Location $RepoRoot
try {
    foreach ($CurrentStage in $SelectedStages) {
        switch ($CurrentStage) {
            "generate" {
                Invoke-ManageCommand -Name "generate_evaluation_variants" -Paid -Arguments @(
                    "--scenarios", $ScenarioPath,
                    "--output", $GenerationPath,
                    "--seed", $GenerationSeed.ToString(),
                    "--model", $GenerationModel,
                    "--workers", $Workers.ToString()
                )
            }
            "prepare" {
                Assert-Artifact -Path $GenerationPath -RequiredFor "prepare"
                Invoke-ManageCommand -Name "prepare_evaluation_packet" -Arguments @(
                    "--scenarios", $ScenarioPath,
                    "--generation", $GenerationPath,
                    "--output-dir", $RatingPath,
                    "--seed", $PacketSeed.ToString()
                )
            }
            "judge" {
                Assert-Artifact -Path $PacketPath -RequiredFor "judge"
                Invoke-ManageCommand -Name "judge_evaluation_packet" -Paid -Arguments @(
                    "--packet", $PacketPath,
                    "--output-dir", $RatingPath,
                    "--seed", $JudgeSeed.ToString(),
                    "--judge-a-model", $JudgeAModel,
                    "--judge-b-model", $JudgeBModel,
                    "--workers", $Workers.ToString()
                )
            }
            "analyze" {
                Assert-Artifact -Path $PacketPath -RequiredFor "analyze"
                Assert-Artifact -Path $PrivateMapPath -RequiredFor "analyze"
                Assert-Artifact -Path $GenerationPath -RequiredFor "analyze"
                Assert-Artifact -Path $JudgeAPath -RequiredFor "analyze"
                Assert-Artifact -Path $JudgeBPath -RequiredFor "analyze"
                Invoke-ManageCommand -Name "analyze_evaluation" -Arguments @(
                    "--packet", $PacketPath,
                    "--private-map", $PrivateMapPath,
                    "--generation", $GenerationPath,
                    "--judge-a", $JudgeAPath,
                    "--judge-b", $JudgeBPath,
                    "--acceptance-rule", "v2",
                    "--output-json", $ResultJsonPath,
                    "--output-markdown", $ResultMarkdownPath
                )
            }
        }
    }
}
finally {
    Pop-Location
}

if ($DryRun) {
    Write-Host "`nDry run complete. No directories, manifests, artifacts, or provider calls were created."
    return
}

if ($SelectedStages -contains "analyze") {
    $Result = Get-Content -LiteralPath $ResultJsonPath -Raw | ConvertFrom-Json
    if ($Result.schema_version -ne 3) {
        throw "Expected V2 result schema version 3, found '$($Result.schema_version)'."
    }
    if ($Result.acceptance_rule_version -ne "comparative_acceptance_v2") {
        throw "Result does not identify comparative_acceptance_v2."
    }

    $Verdict = if ($Result.acceptance_passed) { "PASS" } else { "FAIL" }
    Write-Host "`nV2 benchmark verdict: $Verdict"
    Write-Host "JSON report: $ResultJsonPath"
    Write-Host "Markdown report: $ResultMarkdownPath"
}
