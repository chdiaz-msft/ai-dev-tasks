<#
.SYNOPSIS
Creates local junctions from consumer Git repositories to this canonical checkout.

.DESCRIPTION
Creates a directory junction named .ai-dev-tasks by default in each consumer
repository and adds it to that repository's local .git/info/exclude file.
Running the script again is safe when the existing junction has the same target.

.EXAMPLE
.\link-ai-dev-tasks.ps1 -ConsumerPath C:\git\repo-one

.EXAMPLE
.\link-ai-dev-tasks.ps1 -ConsumerPath C:\git\repo-one,C:\git\repo-two
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(ValueFromPipeline, ValueFromPipelineByPropertyName)]
    [Alias("Path")]
    [string[]] $ConsumerPath = @((Get-Location).Path),

    [string] $CanonicalPath = $PSScriptRoot,

    [ValidateNotNullOrEmpty()]
    [string] $LinkName = ".ai-dev-tasks"
)

begin {
    Set-StrictMode -Version Latest
    $ErrorActionPreference = "Stop"

    if ($LinkName -in @(".", "..") -or
        $LinkName.IndexOfAny([System.IO.Path]::GetInvalidFileNameChars()) -ge 0 -or
        $LinkName.Contains([System.IO.Path]::DirectorySeparatorChar) -or
        $LinkName.Contains([System.IO.Path]::AltDirectorySeparatorChar)) {
        throw "LinkName must be a valid single directory name."
    }

    if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
        throw "NTFS directory junctions are supported only on Windows."
    }

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git was not found on PATH."
    }

    $resolvedCanonicalPath = (Resolve-Path -LiteralPath $CanonicalPath).Path
    if (-not (Test-Path -LiteralPath $resolvedCanonicalPath -PathType Container)) {
        throw "CanonicalPath must identify a directory: $CanonicalPath"
    }

    function Get-NormalizedPath {
        param(
            [Parameter(Mandatory)]
            [string] $Path
        )

        return [System.IO.Path]::GetFullPath($Path).TrimEnd(
            [System.IO.Path]::DirectorySeparatorChar,
            [System.IO.Path]::AltDirectorySeparatorChar
        )
    }

    function Get-GitRepositoryRoot {
        param(
            [Parameter(Mandatory)]
            [string] $Path
        )

        $root = & git -C $Path rev-parse --show-toplevel 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $root) {
            throw "ConsumerPath is not inside a Git working tree: $Path"
        }

        return (Resolve-Path -LiteralPath $root.Trim()).Path
    }

    function Add-GitExcludeEntry {
        param(
            [Parameter(Mandatory)]
            [string] $RepositoryRoot,

            [Parameter(Mandatory)]
            [string] $Entry
        )

        $excludePath = (& git -C $RepositoryRoot rev-parse --git-path info/exclude).Trim()
        if ($LASTEXITCODE -ne 0 -or -not $excludePath) {
            throw "Could not locate the Git exclude file for: $RepositoryRoot"
        }

        if (-not [System.IO.Path]::IsPathRooted($excludePath)) {
            $excludePath = Join-Path $RepositoryRoot $excludePath
        }

        $excludeDirectory = Split-Path -Parent $excludePath
        if (-not (Test-Path -LiteralPath $excludeDirectory)) {
            New-Item -ItemType Directory -Path $excludeDirectory -Force | Out-Null
        }

        $existingEntries = if (Test-Path -LiteralPath $excludePath) {
            @(Get-Content -LiteralPath $excludePath)
        }
        else {
            @()
        }

        if ($existingEntries -notcontains $Entry) {
            Add-Content -LiteralPath $excludePath -Value $Entry
            Write-Verbose "Added '$Entry' to '$excludePath'."
        }
    }
}

process {
    foreach ($path in $ConsumerPath) {
        $consumerRoot = Get-GitRepositoryRoot -Path $path
        $normalizedConsumerRoot = Get-NormalizedPath -Path $consumerRoot
        $normalizedCanonicalPath = Get-NormalizedPath -Path $resolvedCanonicalPath

        if ($normalizedConsumerRoot -eq $normalizedCanonicalPath) {
            throw "The consumer repository cannot be the canonical repository itself."
        }

        $linkPath = Join-Path $consumerRoot $LinkName
        if (Test-Path -LiteralPath $linkPath) {
            $existingItem = Get-Item -LiteralPath $linkPath -Force
            $existingTarget = @($existingItem.Target) | Select-Object -First 1
            $isExpectedJunction =
                $existingItem.LinkType -eq "Junction" -and
                $existingTarget -and
                (Get-NormalizedPath -Path $existingTarget) -eq $normalizedCanonicalPath

            if (-not $isExpectedJunction) {
                throw "The link path already exists and is not the expected junction: $linkPath"
            }
        }
        elseif ($PSCmdlet.ShouldProcess($linkPath, "Create junction to '$resolvedCanonicalPath'")) {
            New-Item -ItemType Junction -Path $linkPath -Target $resolvedCanonicalPath | Out-Null
        }

        $excludeEntry = "/$LinkName/"
        if ($PSCmdlet.ShouldProcess($consumerRoot, "Add '$excludeEntry' to the local Git exclude file")) {
            Add-GitExcludeEntry -RepositoryRoot $consumerRoot -Entry $excludeEntry
        }

        [pscustomobject]@{
            Consumer = $consumerRoot
            Junction = $linkPath
            Target = $resolvedCanonicalPath
            Excluded = $excludeEntry
        }
    }
}
