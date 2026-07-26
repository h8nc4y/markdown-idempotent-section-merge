[CmdletBinding()]
param(
    [string]$Path = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runtimeIsWindows = [Environment]::OSVersion.Platform -eq
    [PlatformID]::Win32NT

$scriptRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($scriptRoot)) {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

if ([string]::IsNullOrWhiteSpace($Path)) {
    $Path = Split-Path -Parent $scriptRoot
}

$root = (Resolve-Path -LiteralPath $Path).Path
$scanner = Join-Path $root 'scripts/scan-private-markers.ps1'
if (-not (Test-Path -LiteralPath $scanner -PathType Leaf)) {
    throw "Missing scanner script: $scanner"
}
$processSupport = Join-Path $root 'scripts/private-marker-process.ps1'
if (-not (Test-Path -LiteralPath $processSupport -PathType Leaf)) {
    throw "Missing bounded process support script: $processSupport"
}
. $processSupport

$currentPowerShellExecutable = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
if ([string]::IsNullOrWhiteSpace($currentPowerShellExecutable) -or
    -not (Test-Path -LiteralPath $currentPowerShellExecutable -PathType Leaf)) {
    $hostExecutableName = if ($PSVersionTable.PSVersion.Major -le 5) {
        'powershell.exe'
    } elseif ($runtimeIsWindows) {
        'pwsh.exe'
    } else {
        'pwsh'
    }
    $currentPowerShellExecutable = Join-Path $PSHOME $hostExecutableName
}
if (-not (Test-Path -LiteralPath $currentPowerShellExecutable -PathType Leaf)) {
    throw "Cannot resolve the current PowerShell host executable: $currentPowerShellExecutable"
}

$failures = New-Object System.Collections.Generic.List[string]

function Add-Failure {
    param([string]$Message)
    $failures.Add($Message) | Out-Null
}

function Get-ProcessEnvironmentSnapshot {
    $snapshot = @{}
    $environment = [Environment]::GetEnvironmentVariables('Process')
    foreach ($name in $environment.Keys) {
        $snapshot["$name"] = [string]$environment[$name]
    }
    return $snapshot
}

function Compare-ProcessEnvironmentSnapshot {
    param([hashtable]$Expected)

    $actual = Get-ProcessEnvironmentSnapshot
    $mismatches = New-Object System.Collections.Generic.List[string]
    foreach ($name in @($Expected.Keys + $actual.Keys) | Sort-Object -Unique) {
        if ($Expected.ContainsKey($name) -ne $actual.ContainsKey($name) -or
            ($Expected.ContainsKey($name) -and $Expected[$name] -cne $actual[$name])) {
            # Never include values: ambient environment data may be sensitive.
            $mismatches.Add($name) | Out-Null
        }
    }
    return @($mismatches)
}

function Assert-ProcessEnvironmentUnchanged {
    param(
        [hashtable]$Expected,
        [string]$Context
    )

    $mismatches = @(Compare-ProcessEnvironmentSnapshot -Expected $Expected)
    if ($mismatches.Count -gt 0) {
        Add-Failure "$Context changed parent environment variables: $($mismatches -join ', ')."
    }
}

function Test-BoundedResultHealthy {
    param([object]$Result)

    return $null -ne $Result -and
        -not $Result.TimedOut -and
        -not $Result.OutputLimitExceeded -and
        $Result.TreeStopped -and
        $Result.StreamsDrained
}

# byte列はtextへ変換せず、長さと各位置の値を厳密に比較する。
function Test-ByteArraysEqual {
    param(
        [AllowNull()]
        [byte[]]$Expected,

        [AllowNull()]
        [byte[]]$Actual
    )

    if ($null -eq $Expected -or $null -eq $Actual -or
        $Expected.Length -ne $Actual.Length) {
        return $false
    }
    for ($byteIndex = 0; $byteIndex -lt $Expected.Length; $byteIndex++) {
        if ($Expected[$byteIndex] -ne $Actual[$byteIndex]) {
            return $false
        }
    }
    return $true
}

# fixtureごとに独立したGit環境を作り、scanner childの時間・出力・子孫停止を観測する。
function Invoke-Scanner {
    param(
        [string]$ScanPath,
        [hashtable]$InheritedEnvironment = @{},
        [int]$MaxStdoutBytes = 16777216,
        [int]$MaxStderrBytes = 1048576
    )

    $arguments = @('-NoProfile')
    if ($PSVersionTable.PSVersion.Major -le 5 -and $runtimeIsWindows) {
        $arguments += @('-ExecutionPolicy', 'Bypass')
    }
    $arguments += @('-File', $scanner, '-Path', $ScanPath)

    $scannerIsolationRoot = Join-Path (
        [System.IO.Path]::GetTempPath()
    ) ("markdown-idempotent-section-merge-scanner-git-" +
        [System.Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $scannerIsolationRoot | Out-Null
    try {
        $result = Invoke-PrivateMarkerBoundedProcess `
            -FileName $currentPowerShellExecutable `
            -Arguments $arguments `
            -IsolationRoot $scannerIsolationRoot `
            -InheritedEnvironment $InheritedEnvironment `
            -TimeoutMilliseconds 30000 `
            -MaxStdoutBytes $MaxStdoutBytes `
            -MaxStderrBytes $MaxStderrBytes `
            -PassThroughGitEnvironment
        if (-not (Test-BoundedResultHealthy -Result $result)) {
            Add-Failure 'Scanner child exceeded its bounded runtime for a synthetic fixture.'
        }
        return $result
    }
    finally {
        if (Test-Path -LiteralPath $scannerIsolationRoot) {
            Remove-Item -LiteralPath $scannerIsolationRoot -Recurse -Force
        }
    }
}

# synthetic Git操作もproduction同等のbounded process境界を必ず通す。
function Invoke-IsolatedGit {
    param(
        [Parameter(Mandatory = $true)]
        [string]$GitPath,

        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [string]$IsolationRoot,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [hashtable]$InheritedEnvironment = @{}
    )

    return Invoke-PrivateMarkerBoundedProcess `
        -FileName $GitPath `
        -Arguments (@('-C', $WorkingDirectory) + $Arguments) `
        -IsolationRoot $IsolationRoot `
        -WorkingDirectory $WorkingDirectory `
        -InheritedEnvironment $InheritedEnvironment `
        -TimeoutMilliseconds 20000
}

# final equality再検証の狭い競合窓を再現するため、index swapをmarkerで同期する。
function Start-SynchronizedIndexMutator {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ReadyPath,

        [Parameter(Mandatory = $true)]
        [string]$ReleasePath,

        [Parameter(Mandatory = $true)]
        [string]$SwapPath,

        [Parameter(Mandatory = $true)]
        [string]$IndexPath,

        [Parameter(Mandatory = $true)]
        [string]$BackupPath
    )

    $escapedReady = $ReadyPath.Replace("'", "''")
    $escapedRelease = $ReleasePath.Replace("'", "''")
    $escapedSwap = $SwapPath.Replace("'", "''")
    $escapedIndex = $IndexPath.Replace("'", "''")
    $escapedBackup = $BackupPath.Replace("'", "''")
    $mutatorScript = @"
`$readyObserved = `$false
for (`$attempt = 0; `$attempt -lt 1500; `$attempt++) {
    if ([IO.File]::Exists('$escapedReady')) {
        `$readyObserved = `$true
        break
    }
    Start-Sleep -Milliseconds 10
}
if (-not `$readyObserved) {
    exit 3
}
[IO.File]::Replace(
    '$escapedSwap',
    '$escapedIndex',
    '$escapedBackup'
)
[IO.File]::WriteAllText(
    '$escapedRelease',
    'release',
    [Text.UTF8Encoding]::new(`$false)
)
"@
    $mutatorEncoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($mutatorScript)
    )
    $mutatorArguments = @('-NoProfile')
    if ($PSVersionTable.PSVersion.Major -le 5 -and $runtimeIsWindows) {
        $mutatorArguments += @('-ExecutionPolicy', 'Bypass')
    }
    $mutatorArguments += @('-EncodedCommand', $mutatorEncoded)
    $mutatorInfo = New-Object Diagnostics.ProcessStartInfo
    $mutatorInfo.FileName = $currentPowerShellExecutable
    $mutatorInfo.UseShellExecute = $false
    $mutatorInfo.CreateNoWindow = $true
    $mutatorArgumentList =
        $mutatorInfo.PSObject.Properties['ArgumentList']
    if ($null -ne $mutatorArgumentList) {
        foreach ($mutatorArgument in $mutatorArguments) {
            $mutatorInfo.ArgumentList.Add($mutatorArgument)
        }
    } else {
        $mutatorInfo.Arguments = (
            $mutatorArguments | ForEach-Object {
                ConvertTo-PrivateMarkerProcessArgument -Argument $_
            }
        ) -join ' '
    }
    return [Diagnostics.Process]::Start($mutatorInfo)
}

$tempRoot = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("markdown-idempotent-section-merge-scan-test-" +
    [System.Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempRoot | Out-Null

try {
    # Windowsの最初の呼出しはowned Job外から始まり、suspended processを
    # Jobへ割り当ててからresumeするatomic経路を必ず通る。
    # 0x80/0xFFを含むnative streamをtext decode/re-encodeしないことも先頭で固定する。
    $byteExactIsolationRoot = Join-Path $tempRoot 'byte-exact-first-launch'
    New-Item -ItemType Directory -Path $byteExactIsolationRoot | Out-Null
    $byteExactExpected = [byte[]]@(0, 128, 255)
    $pythonCommandName = if ($runtimeIsWindows) { 'python' } else { 'python3' }
    $pythonCommand = Get-Command `
        $pythonCommandName `
        -CommandType Application `
        -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $pythonCommand) {
        throw "Missing required native byte-probe host: $pythonCommandName"
    }
    $byteExactChildScript = @'
import sys
expected = bytes((0, 128, 255))
actual = sys.stdin.buffer.read(len(expected))
sys.stdout.buffer.write(actual)
sys.stdout.buffer.flush()
sys.stderr.buffer.write(expected)
sys.stderr.buffer.flush()
raise SystemExit(0 if actual == expected else 91)
'@
    $byteExactArguments = @('-c', $byteExactChildScript)
    $byteExactResult = Invoke-PrivateMarkerBoundedProcess `
        -FileName $pythonCommand.Source `
        -Arguments $byteExactArguments `
        -IsolationRoot $byteExactIsolationRoot `
        -StandardInputBytes $byteExactExpected `
        -TimeoutMilliseconds 15000 `
        -MaxStdoutBytes 1024 `
        -MaxStderrBytes 1024
    if (-not (Test-BoundedResultHealthy -Result $byteExactResult) -or
        $byteExactResult.ExitCode -ne 0) {
        Add-Failure 'Expected the initial atomic Windows launch byte probe to exit cleanly.'
    }
    if (-not (Test-ByteArraysEqual `
        -Expected $byteExactExpected `
        -Actual $byteExactResult.StdoutBytes)) {
        Add-Failure 'Expected stdin/stdout bytes 00/80/FF to remain byte-exact through the initial atomic Windows launch.'
    }
    if (-not (Test-ByteArraysEqual `
        -Expected $byteExactExpected `
        -Actual $byteExactResult.StderrBytes)) {
        Add-Failure 'Expected stderr bytes 00/80/FF to remain byte-exact through the initial atomic Windows launch.'
    }

    # A delayed grandchild sentinel distinguishes true tree cleanup from a
    # parent-only kill. The grandchild also keeps the redirected pipe open.
    $timeoutIsolationRoot = Join-Path $tempRoot 'timeout-isolation'
    $timeoutSentinel = Join-Path $tempRoot 'grandchild-survived-timeout'
    $escapedTimeoutSentinel = $timeoutSentinel.Replace("'", "''")
    $grandchildScript = @"
Start-Sleep -Milliseconds 1500
[System.IO.File]::WriteAllText('$escapedTimeoutSentinel', 'survived')
"@
    $grandchildEncoded = [Convert]::ToBase64String(
        [System.Text.Encoding]::Unicode.GetBytes($grandchildScript)
    )
    $escapedPowerShellExecutable = $currentPowerShellExecutable.Replace("'", "''")
    $parentScript = @"
& '$escapedPowerShellExecutable' -NoProfile -EncodedCommand '$grandchildEncoded'
Start-Sleep -Seconds 30
"@
    $parentEncoded = [Convert]::ToBase64String(
        [System.Text.Encoding]::Unicode.GetBytes($parentScript)
    )
    $timeoutArguments = @('-NoProfile')
    if ($PSVersionTable.PSVersion.Major -le 5 -and $runtimeIsWindows) {
        $timeoutArguments += @('-ExecutionPolicy', 'Bypass')
    }
    $timeoutArguments += @('-EncodedCommand', $parentEncoded)
    $beforeTimeoutEnvironment = Get-ProcessEnvironmentSnapshot
    $timeoutResult = Invoke-PrivateMarkerBoundedProcess `
        -FileName $currentPowerShellExecutable `
        -Arguments $timeoutArguments `
        -IsolationRoot $timeoutIsolationRoot `
        -TimeoutMilliseconds 300
    if (-not $timeoutResult.TimedOut -or
        -not $timeoutResult.TreeStopped -or
        -not $timeoutResult.StreamsDrained) {
        Add-Failure 'Expected the bounded child regression to time out and stop its process tree.'
    }
    for ($attempt = 0; $attempt -lt 25 -and
        -not (Test-Path -LiteralPath $timeoutSentinel); $attempt++) {
        Start-Sleep -Milliseconds 100
    }
    if (Test-Path -LiteralPath $timeoutSentinel) {
        Add-Failure 'Expected timeout cleanup to kill the grandchild before its delayed sentinel write.'
    }
    Assert-ProcessEnvironmentUnchanged `
        -Expected $beforeTimeoutEnvironment `
        -Context 'Bounded child timeout'

    # The direct child can exit before a grandchild that inherited stdout. A
    # kill-on-close job/process group must remain addressable after that exit;
    # otherwise stream draining times out and the delayed sentinel survives.
    $detachedSentinel = Join-Path $tempRoot 'detached-grandchild-survived'
    $escapedDetachedSentinel = $detachedSentinel.Replace("'", "''")
    $detachedGrandchildScript = @"
Start-Sleep -Milliseconds 1500
[System.IO.File]::WriteAllText('$escapedDetachedSentinel', 'survived')
[Console]::Out.Write('late-output')
"@
    $detachedGrandchildEncoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($detachedGrandchildScript)
    )
    $detachedParentScript = @"
`$startInfo = New-Object System.Diagnostics.ProcessStartInfo
`$startInfo.FileName = '$escapedPowerShellExecutable'
`$startInfo.Arguments = '-NoProfile -EncodedCommand $detachedGrandchildEncoded'
`$startInfo.UseShellExecute = `$false
`$startInfo.CreateNoWindow = `$true
`$child = [System.Diagnostics.Process]::Start(`$startInfo)
`$child.Dispose()
"@
    $detachedParentEncoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($detachedParentScript)
    )
    $detachedArguments = @('-NoProfile')
    if ($PSVersionTable.PSVersion.Major -le 5 -and $runtimeIsWindows) {
        $detachedArguments += @('-ExecutionPolicy', 'Bypass')
    }
    $detachedArguments += @('-EncodedCommand', $detachedParentEncoded)
    $detachedResult = Invoke-PrivateMarkerBoundedProcess `
        -FileName $currentPowerShellExecutable `
        -Arguments $detachedArguments `
        -IsolationRoot (Join-Path $tempRoot 'detached-isolation') `
        -TimeoutMilliseconds 5000 `
        -DrainTimeoutMilliseconds 3000 `
        -ForceNativePosixSessionGate:(-not $runtimeIsWindows)
    if ($detachedResult.TimedOut -or
        -not $detachedResult.TreeStopped -or
        -not $detachedResult.StreamsDrained) {
        Add-Failure 'Expected parent-first exit cleanup to stop the pipe-owning grandchild and drain both streams.'
    }
    for ($attempt = 0; $attempt -lt 25 -and
        -not (Test-Path -LiteralPath $detachedSentinel); $attempt++) {
        Start-Sleep -Milliseconds 100
    }
    if (Test-Path -LiteralPath $detachedSentinel) {
        Add-Failure 'Expected parent-first exit cleanup to kill the grandchild before its delayed sentinel write.'
    }

    # Repeat the zero-wait spawn pattern so the Windows suspended launch and
    # POSIX process group prove the race is closed rather than merely rare.
    $immediateRaceSentinels =
        New-Object System.Collections.Generic.List[string]
    for ($raceAttempt = 1; $raceAttempt -le 10; $raceAttempt++) {
        $raceSentinel = Join-Path `
            $tempRoot `
            "immediate-grandchild-survived-$raceAttempt"
        $immediateRaceSentinels.Add($raceSentinel) | Out-Null
        $escapedRaceSentinel = $raceSentinel.Replace("'", "''")
        $raceGrandchildScript = @"
Start-Sleep -Milliseconds 1000
[IO.File]::WriteAllText('$escapedRaceSentinel', 'survived')
"@
        $raceGrandchildEncoded = [Convert]::ToBase64String(
            [Text.Encoding]::Unicode.GetBytes($raceGrandchildScript)
        )
        $raceParentScript = @"
`$startInfo = New-Object Diagnostics.ProcessStartInfo
`$startInfo.FileName = '$escapedPowerShellExecutable'
`$startInfo.Arguments = '-NoProfile -EncodedCommand $raceGrandchildEncoded'
`$startInfo.UseShellExecute = `$false
`$startInfo.CreateNoWindow = `$true
`$child = [Diagnostics.Process]::Start(`$startInfo)
`$child.Dispose()
"@
        $raceParentEncoded = [Convert]::ToBase64String(
            [Text.Encoding]::Unicode.GetBytes($raceParentScript)
        )
        $raceArguments = @('-NoProfile')
        if ($PSVersionTable.PSVersion.Major -le 5 -and
            $runtimeIsWindows) {
            $raceArguments += @('-ExecutionPolicy', 'Bypass')
        }
        $raceArguments += @('-EncodedCommand', $raceParentEncoded)
        $raceResult = Invoke-PrivateMarkerBoundedProcess `
            -FileName $currentPowerShellExecutable `
            -Arguments $raceArguments `
            -IsolationRoot (
                Join-Path $tempRoot "immediate-race-isolation-$raceAttempt"
            ) `
            -TimeoutMilliseconds 5000 `
            -DrainTimeoutMilliseconds 3000 `
            -ForceNativePosixSessionGate:(
                -not $runtimeIsWindows -and $raceAttempt -eq 1
            )
        if ($raceResult.TimedOut -or
            -not $raceResult.TreeStopped -or
            -not $raceResult.StreamsDrained) {
            Add-Failure "Expected immediate-spawn race attempt $raceAttempt to stop its full process tree."
        }
    }
    Start-Sleep -Milliseconds 1300
    foreach ($raceSentinel in $immediateRaceSentinels) {
        if (Test-Path -LiteralPath $raceSentinel) {
            Add-Failure 'Expected all 10 immediate-spawn process-tree attempts to suppress delayed sentinels.'
            break
        }
    }
    if (-not $runtimeIsWindows) {
        # kill(2) returns -1 for both ESRCH and EPERM. Only ESRCH is a
        # successful "already gone" cleanup state; permission and other
        # failures must keep TreeStopped false.
        if (-not [MarkdownIdempotentSectionMerge.PrivateMarkerPosixSignal]::
                IsSuccessfulResult(0, 0) -or
            -not [MarkdownIdempotentSectionMerge.PrivateMarkerPosixSignal]::
                IsSuccessfulResult(-1, 3) -or
            [MarkdownIdempotentSectionMerge.PrivateMarkerPosixSignal]::
                IsSuccessfulResult(-1, 1) -or
            [MarkdownIdempotentSectionMerge.PrivateMarkerPosixSignal]::
                IsSuccessfulResult(-1, 13)) {
            Add-Failure 'Expected POSIX group cleanup to accept success/ESRCH and reject EPERM/EACCES.'
        }
    }

    # The raw budget includes every serialized byte, including a visible prefix
    # and the platform's real newline (CRLF on Windows, LF on POSIX).
    $exactBudgetScript = @'
$prefixBytes = [System.Text.Encoding]::UTF8.GetBytes('scan-prefix:')
$newlineBytes = [System.Text.Encoding]::UTF8.GetBytes([Environment]::NewLine)
$fillLength = 128 - $prefixBytes.Length - $newlineBytes.Length
$fillBytes = [System.Text.Encoding]::UTF8.GetBytes(('x' * $fillLength))
$stream = [Console]::OpenStandardOutput()
$stream.Write($prefixBytes, 0, $prefixBytes.Length)
$stream.Write($fillBytes, 0, $fillBytes.Length)
$stream.Write($newlineBytes, 0, $newlineBytes.Length)
$stream.Flush()
'@
    $exactBudgetEncoded = [Convert]::ToBase64String(
        [System.Text.Encoding]::Unicode.GetBytes($exactBudgetScript)
    )
    $exactBudgetArguments = @('-NoProfile')
    if ($PSVersionTable.PSVersion.Major -le 5 -and $runtimeIsWindows) {
        $exactBudgetArguments += @('-ExecutionPolicy', 'Bypass')
    }
    $exactBudgetArguments += @('-EncodedCommand', $exactBudgetEncoded)
    $exactBudgetResult = Invoke-PrivateMarkerBoundedProcess `
        -FileName $currentPowerShellExecutable `
        -Arguments $exactBudgetArguments `
        -IsolationRoot (Join-Path $tempRoot 'exact-budget-isolation') `
        -TimeoutMilliseconds 5000 `
        -MaxStdoutBytes 128 `
        -MaxStderrBytes 65536
    if (-not (Test-BoundedResultHealthy -Result $exactBudgetResult) -or
        $exactBudgetResult.ExitCode -ne 0 -or
        $exactBudgetResult.StdoutBytes.Length -ne 128) {
        Add-Failure 'Expected prefix plus the platform newline to fit an exact 128-byte raw stdout budget.'
    }

    # A child that floods stdout must be stopped by the byte cap before the
    # runtime timeout, and retained output must never exceed the configured cap.
    $limitScript = @'
[Console]::Out.Write(('x' * 4096))
Start-Sleep -Seconds 30
'@
    $limitEncoded = [Convert]::ToBase64String(
        [System.Text.Encoding]::Unicode.GetBytes($limitScript)
    )
    $limitArguments = @('-NoProfile')
    if ($PSVersionTable.PSVersion.Major -le 5 -and $runtimeIsWindows) {
        $limitArguments += @('-ExecutionPolicy', 'Bypass')
    }
    $limitArguments += @('-EncodedCommand', $limitEncoded)
    $limitResult = Invoke-PrivateMarkerBoundedProcess `
        -FileName $currentPowerShellExecutable `
        -Arguments $limitArguments `
        -IsolationRoot (Join-Path $tempRoot 'limit-isolation') `
        -TimeoutMilliseconds 5000 `
        -MaxStdoutBytes 128 `
        -MaxStderrBytes 128
    if (-not $limitResult.OutputLimitExceeded -or
        $limitResult.TimedOut -or
        -not $limitResult.TreeStopped -or
        -not $limitResult.StreamsDrained -or
        $limitResult.StdoutBytes.Length -gt 128) {
        Add-Failure 'Expected stdout byte overflow to stop the child tree and stay within the configured output cap.'
    }

    # Prove the child-only Git boundary disables replace refs, lazy object
    # fetching, and all transport protocols even when the caller asks for the
    # opposite. The probe emits booleans only; it never prints ambient values.
    $gitBoundaryScript = @'
$protocolDenied = $false
for ($index = 0; $index -lt [int]$env:GIT_CONFIG_COUNT; $index++) {
    if ([Environment]::GetEnvironmentVariable("GIT_CONFIG_KEY_$index") -eq 'protocol.allow' -and
        [Environment]::GetEnvironmentVariable("GIT_CONFIG_VALUE_$index") -eq 'never') {
        $protocolDenied = $true
    }
}
[Console]::Out.Write(
    (($env:GIT_NO_REPLACE_OBJECTS -eq '1').ToString()) + '|' +
    (($env:GIT_NO_LAZY_FETCH -eq '1').ToString()) + '|' +
    $protocolDenied.ToString()
)
'@
    $gitBoundaryEncoded = [Convert]::ToBase64String(
        [System.Text.Encoding]::Unicode.GetBytes($gitBoundaryScript)
    )
    $gitBoundaryArguments = @('-NoProfile')
    if ($PSVersionTable.PSVersion.Major -le 5 -and $runtimeIsWindows) {
        $gitBoundaryArguments += @('-ExecutionPolicy', 'Bypass')
    }
    $gitBoundaryArguments += @('-EncodedCommand', $gitBoundaryEncoded)
    $gitBoundaryResult = Invoke-PrivateMarkerBoundedProcess `
        -FileName $currentPowerShellExecutable `
        -Arguments $gitBoundaryArguments `
        -IsolationRoot (Join-Path $tempRoot 'git-boundary-isolation') `
        -InheritedEnvironment @{
            GIT_NO_REPLACE_OBJECTS = '0'
            GIT_NO_LAZY_FETCH = '0'
        } `
        -TimeoutMilliseconds 5000 `
        -MaxStdoutBytes 128 `
        -MaxStderrBytes 65536
    $gitBoundaryText = [Text.Encoding]::UTF8.GetString(
        $gitBoundaryResult.StdoutBytes
    )
    if (-not (Test-BoundedResultHealthy -Result $gitBoundaryResult) -or
        $gitBoundaryResult.ExitCode -ne 0 -or
        $gitBoundaryText -ne 'True|True|True') {
        Add-Failure "Expected the child-only Git environment to disable replace refs, lazy fetch, and protocols (exit $($gitBoundaryResult.ExitCode), stdout '$gitBoundaryText', timeout $($gitBoundaryResult.TimedOut), limit $($gitBoundaryResult.OutputLimitExceeded), tree $($gitBoundaryResult.TreeStopped), streams $($gitBoundaryResult.StreamsDrained))."
    }

    if ($runtimeIsWindows) {
        # CreateProcessWでsuspendしたtargetをJobへ割り当て、resumeした後も
        # target自身が同じJobに所属していることをprocess内から検証する。
        $jobIdentityScript = @'
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class SyntheticJobIdentityProbe
{
    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool IsProcessInJob(
        IntPtr process,
        IntPtr job,
        out bool result
    );
    public static bool IsInJob()
    {
        bool result;
        return IsProcessInJob(
            GetCurrentProcess(),
            IntPtr.Zero,
            out result
        ) && result;
    }
}
"@
[Console]::Out.Write(
    [SyntheticJobIdentityProbe]::IsInJob().ToString()
)
'@
        $jobIdentityEncoded = [Convert]::ToBase64String(
            [Text.Encoding]::Unicode.GetBytes($jobIdentityScript)
        )
        $jobIdentityResult = Invoke-PrivateMarkerBoundedProcess `
            -FileName $currentPowerShellExecutable `
            -Arguments @(
                '-NoProfile',
                '-ExecutionPolicy',
                'Bypass',
                '-EncodedCommand',
                $jobIdentityEncoded
            ) `
            -IsolationRoot (Join-Path $tempRoot 'job-identity-isolation') `
            -TimeoutMilliseconds 10000 `
            -MaxStdoutBytes 64 `
            -MaxStderrBytes 65536
        $jobIdentityText = [Text.Encoding]::UTF8.GetString(
            $jobIdentityResult.StdoutBytes
        )
        if (-not (Test-BoundedResultHealthy -Result $jobIdentityResult) -or
            $jobIdentityResult.ExitCode -ne 0 -or
            $jobIdentityText -ne 'True') {
            Add-Failure "Expected the actual gated target process to inherit the assigned Windows Job (exit $($jobIdentityResult.ExitCode), stdout '$jobIdentityText', timeout $($jobIdentityResult.TimedOut), limit $($jobIdentityResult.OutputLimitExceeded), tree $($jobIdentityResult.TreeStopped), streams $($jobIdentityResult.StreamsDrained))."
        }

        # Job 割当前とresume前のsynthetic failureはtargetを一度も実行せず、
        # cleanup APIの確認後にPIDを有限時間でprocess tableから除去する。
        foreach ($launchFailureMode in @('assign', 'resume')) {
            $launchFailureSentinel = Join-Path `
                $tempRoot `
                "windows-launch-failure-$launchFailureMode"
            $escapedLaunchFailureSentinel =
                $launchFailureSentinel.Replace("'", "''")
            $launchFailureScript = @"
[IO.File]::WriteAllText('$escapedLaunchFailureSentinel', 'ran')
"@
            $launchFailureEncoded = [Convert]::ToBase64String(
                [Text.Encoding]::Unicode.GetBytes($launchFailureScript)
            )
            $launchFailureStopwatch =
                [Diagnostics.Stopwatch]::StartNew()
            $launchFailureObserved = $false
            try {
                [void](Invoke-PrivateMarkerBoundedProcess `
                    -FileName $currentPowerShellExecutable `
                    -Arguments @(
                        '-NoProfile',
                        '-ExecutionPolicy',
                        'Bypass',
                        '-EncodedCommand',
                        $launchFailureEncoded
                    ) `
                    -IsolationRoot (
                        Join-Path `
                            $tempRoot `
                            "windows-launch-$launchFailureMode-isolation"
                    ) `
                    -TimeoutMilliseconds 10000 `
                    -ForceWindowsLaunchFailure $launchFailureMode)
            }
            catch {
                $launchFailureObserved = $true
            }
            $launchFailureStopwatch.Stop()
            $launchFailureProcessId =
                [MarkdownSectionMergeContainedProcess]::
                    LastSyntheticFailureProcessId
            $launchFailureProcessGone = $false
            if ($launchFailureProcessId -gt 0) {
                # API結果の確認に加え、kernel process tableからの消失を
                # 最大1秒だけ再確認し、handleだけ閉じた誤実装を検出する。
                for ($pidCheckAttempt = 0;
                    $pidCheckAttempt -lt 20;
                    $pidCheckAttempt++) {
                    if ($null -eq (Get-Process `
                        -Id $launchFailureProcessId `
                        -ErrorAction SilentlyContinue)) {
                        $launchFailureProcessGone = $true
                        break
                    }
                    Start-Sleep -Milliseconds 50
                }
            }
            Start-Sleep -Milliseconds 100
            if (-not $launchFailureObserved -or
                $launchFailureProcessId -le 0 -or
                -not $launchFailureProcessGone -or
                $launchFailureStopwatch.ElapsedMilliseconds -ge 6000 -or
                (Test-Path -LiteralPath $launchFailureSentinel)) {
                Add-Failure "Expected $launchFailureMode launch failure to remove its PID without resuming the suspended target."
            }
        }
    }

    # The ambient OS variable is untrusted. A fresh child must derive the same
    # kernel branch from .NET even when OS is forged to the opposite platform.
    $forgedOsValue = if ($runtimeIsWindows) {
        'synthetic-posix'
    } else {
        'Windows_NT'
    }
    $escapedProcessSupport = $processSupport.Replace("'", "''")
    $platformProbeScript = @"
. '$escapedProcessSupport'
[Console]::Out.Write(
    `$script:privateMarkerIsWindows.ToString()
)
"@
    $platformProbeEncoded = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($platformProbeScript)
    )
    $platformProbeArguments = @('-NoProfile')
    if ($PSVersionTable.PSVersion.Major -le 5 -and $runtimeIsWindows) {
        $platformProbeArguments += @('-ExecutionPolicy', 'Bypass')
    }
    $platformProbeArguments += @(
        '-EncodedCommand',
        $platformProbeEncoded
    )
    $platformProbeResult = Invoke-PrivateMarkerBoundedProcess `
        -FileName $currentPowerShellExecutable `
        -Arguments $platformProbeArguments `
        -IsolationRoot (Join-Path `
            $tempRoot `
            'platform-probe-isolation') `
        -InheritedEnvironment @{ OS = $forgedOsValue } `
        -TimeoutMilliseconds 10000 `
        -MaxStdoutBytes 32 `
        -MaxStderrBytes 65536
    $platformProbeText = [Text.Encoding]::UTF8.GetString(
        $platformProbeResult.StdoutBytes
    )
    if (-not (Test-BoundedResultHealthy -Result $platformProbeResult) -or
        $platformProbeResult.ExitCode -ne 0 -or
        $platformProbeText -ne $runtimeIsWindows.ToString()) {
        Add-Failure "Expected runtime platform detection to ignore a forged OS variable (exit $($platformProbeResult.ExitCode), stdout '$platformProbeText')."
    }

    # The spaced directory also exercises the PS5.1 native argument fallback.
    $cleanRoot = Join-Path $tempRoot 'clean fixture'
    New-Item -ItemType Directory -Path $cleanRoot | Out-Null
    Set-Content -LiteralPath (Join-Path $cleanRoot 'README.md') -Value @(
        '# Clean synthetic fixture'
        'A completion notice is a claim, not evidence. Verify artifacts first.'
    ) -Encoding UTF8

    $cleanResult = Invoke-Scanner -ScanPath $cleanRoot
    if ($cleanResult.ExitCode -ne 0) {
        Add-Failure "Expected clean fixture to pass, but scanner exited $($cleanResult.ExitCode): $($cleanResult.Output.Trim())"
    }

    # hostileな不存在Pathは固定codeで失敗させ、生pathとUnicode方向制御を漏らさない。
    # stdout/stderrの双方をbyte cap内に保つところまで回帰で固定する。
    $hostileDirectionControl = [char]0x202E
    $hostileLineSeparator = [char]0x2028
    $hostileMissingPath = Join-Path $tempRoot (
        'missing-' + $hostileDirectionControl + $hostileLineSeparator + 'leaf'
    )
    $hostileMissingResult = Invoke-Scanner `
        -ScanPath $hostileMissingPath `
        -MaxStdoutBytes 256 `
        -MaxStderrBytes 65536
    $hostileMissingStdout = [System.Text.Encoding]::UTF8.GetString(
        $hostileMissingResult.StdoutBytes
    )
    $hostileMissingStderr = [System.Text.Encoding]::UTF8.GetString(
        $hostileMissingResult.StderrBytes
    )
    $hostileMissingDiagnostic = $hostileMissingStdout.TrimEnd(
        [char[]]@(13, 10)
    )
    if (-not (Test-BoundedResultHealthy -Result $hostileMissingResult) -or
        $hostileMissingResult.ExitCode -ne 2 -or
        $hostileMissingDiagnostic -ne
            'Private marker scan failed closed (integrity: scan-root-missing).' -or
        $hostileMissingResult.StdoutBytes.Length -gt 256 -or
        $hostileMissingResult.StderrBytes.Length -gt 65536 -or
        $hostileMissingStdout.Contains($hostileMissingPath) -or
        $hostileMissingStderr.Contains($hostileMissingPath) -or
        $hostileMissingStdout.Contains([string]$hostileDirectionControl) -or
        $hostileMissingStderr.Contains([string]$hostileDirectionControl) -or
        $hostileMissingStdout.Contains([string]$hostileLineSeparator) -or
        $hostileMissingStderr.Contains([string]$hostileLineSeparator)) {
        Add-Failure 'Expected a hostile nonexistent path to produce only the bounded fixed scan-root-missing diagnostic.'
    }

    $forgedOsScannerResult = Invoke-Scanner `
        -ScanPath $cleanRoot `
        -InheritedEnvironment @{ OS = $forgedOsValue }
    if ($forgedOsScannerResult.ExitCode -ne 0) {
        Add-Failure "Expected scanner platform selection to ignore a forged OS variable. Output: $($forgedOsScannerResult.Output.Trim())"
    }

    $markerRoot = Join-Path $tempRoot 'marker'
    New-Item -ItemType Directory -Path $markerRoot | Out-Null
    $syntheticMarker = ('g' + 'hp_') + 'synthetic_placeholder_only'
    Set-Content -LiteralPath (Join-Path $markerRoot 'leak.txt') -Value "synthetic marker: $syntheticMarker" -Encoding UTF8

    $markerResult = Invoke-Scanner -ScanPath $markerRoot
    if ($markerResult.ExitCode -eq 0) {
        Add-Failure 'Expected synthetic marker fixture to fail, but scanner exited 0.'
    }
    if ($markerResult.Output -notmatch 'github-classic-token-prefix') {
        Add-Failure "Expected synthetic marker output to name github-classic-token-prefix. Output: $($markerResult.Output.Trim())"
    }

    # Encoding regression: a marker directly after a multi-byte character must
    # still be detected when the scanner runs under Windows PowerShell 5.1.
    # Without an explicit UTF-8 read, 5.1 decodes BOM-less UTF-8 as ANSI and a
    # misread multi-byte character swallows the following ASCII bytes
    # (measured false negative). The fixture is written BOM-less on purpose;
    # the character is built from a code point to keep this file ASCII-only.
    $encodingRoot = Join-Path $tempRoot 'multibyte-adjacent'
    New-Item -ItemType Directory -Path $encodingRoot | Out-Null
    $multiByteContent = 'token after multi-byte char: ' + [char]0x3042 + $syntheticMarker
    [System.IO.File]::WriteAllText((Join-Path $encodingRoot 'leak.md'), $multiByteContent, [System.Text.UTF8Encoding]::new($false))
    $encodingResult = Invoke-Scanner -ScanPath $encodingRoot
    if ($encodingResult.ExitCode -eq 0) {
        Add-Failure 'Expected multi-byte-adjacent marker fixture to fail, but scanner exited 0.'
    }
    if ($encodingResult.Output -notmatch 'github-classic-token-prefix') {
        Add-Failure "Expected multi-byte-adjacent output to name github-classic-token-prefix. Output: $($encodingResult.Output.Trim())"
    }

    # `.env`はGetExtension上の扱いが特殊で、POSIXではhiddenにもなる。
    # 非Git fallbackでも`-Force`とsensitive-name判定を通ることを固定する。
    $dotfileRoot = Join-Path $tempRoot 'dotfile'
    New-Item -ItemType Directory -Path $dotfileRoot | Out-Null
    Set-Content -LiteralPath (Join-Path $dotfileRoot '.env') -Value "value: $syntheticMarker" -Encoding UTF8
    $dotfileResult = Invoke-Scanner -ScanPath $dotfileRoot
    if ($dotfileResult.Output -notmatch 'working-tree') {
        Add-Failure "Expected .env fixture to use working-tree mode. Output: $($dotfileResult.Output.Trim())"
    }
    if ($dotfileResult.ExitCode -eq 0) {
        Add-Failure 'Expected .env dotfile fixture to fail, but scanner exited 0.'
    }
    if ($dotfileResult.Output -notmatch 'github-classic-token-prefix') {
        Add-Failure "Expected .env dotfile output to name github-classic-token-prefix. Output: $($dotfileResult.Output.Trim())"
    }
    if ($dotfileResult.Output -notmatch '\.env') {
        Add-Failure "Expected .env dotfile output to name .env. Output: $($dotfileResult.Output.Trim())"
    }
    if ($dotfileResult.Output.Contains($syntheticMarker)) {
        Add-Failure 'Expected .env dotfile finding to stay redacted, but the raw marker leaked into output.'
    }

    # The working-tree fallback must use POSIX-aware exclusion boundaries and
    # emit a normalized relative path. A real finding outside excluded trees
    # proves the scan ran; marker files inside those trees must remain absent.
    $boundaryRoot = Join-Path $tempRoot 'working-tree-boundaries'
    $boundaryLeakDirectory = Join-Path $boundaryRoot (Join-Path 'nested' 'deep')
    New-Item -ItemType Directory -Path $boundaryLeakDirectory -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $boundaryLeakDirectory 'leak.md') -Value "value: $syntheticMarker" -Encoding UTF8

    foreach ($excludedDirectoryName in @('node_modules', '.cache')) {
        $excludedDirectory = Join-Path $boundaryRoot $excludedDirectoryName
        New-Item -ItemType Directory -Path $excludedDirectory -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $excludedDirectory 'ignored.md') -Value "value: $syntheticMarker" -Encoding UTF8
    }
    $nestedGitDirectory = Join-Path $boundaryRoot (Join-Path 'neutral' '.git')
    New-Item -ItemType Directory -Path $nestedGitDirectory -Force | Out-Null
    Set-Content `
        -LiteralPath (Join-Path $nestedGitDirectory 'ignored.md') `
        -Value "value: $syntheticMarker" `
        -Encoding UTF8
    $nestedGitFileDirectory = Join-Path $boundaryRoot 'neutral-file'
    New-Item `
        -ItemType Directory `
        -Path $nestedGitFileDirectory `
        -Force | Out-Null
    $nestedGitFileMarker = ('g' + 'hp_') +
        'synthetic_nested_git_file'
    Set-Content `
        -LiteralPath (Join-Path $nestedGitFileDirectory '.git') `
        -Value "gitdir: $nestedGitFileMarker" `
        -Encoding UTF8

    $boundaryResult = Invoke-Scanner -ScanPath $boundaryRoot
    if ($boundaryResult.Output -notmatch 'working-tree') {
        Add-Failure "Expected exclusion fixture to use working-tree mode. Output: $($boundaryResult.Output.Trim())"
    }
    if ($boundaryResult.ExitCode -eq 0) {
        Add-Failure 'Expected the non-excluded nested marker to fail the working-tree scan, but scanner exited 0.'
    }
    if ($boundaryResult.Output -notmatch 'nested/deep/leak\.md') {
        Add-Failure "Expected normalized relative path nested/deep/leak.md. Output: $($boundaryResult.Output.Trim())"
    }
    if ($boundaryResult.Output.Contains($boundaryRoot)) {
        Add-Failure "Expected working-tree findings to omit the absolute fixture root. Output: $($boundaryResult.Output.Trim())"
    }
    if ($boundaryResult.Output -match
        '(node_modules|\.cache|\.git)[\\/]ignored\.md' -or
        $boundaryResult.Output -match 'neutral-file[\\/]\.git') {
        Add-Failure "Expected nested .git files/directories, node_modules, and .cache marker files to stay excluded. Output: $($boundaryResult.Output.Trim())"
    }

    # scan root直下の`.git`はfallback除外対象ではない。directory/fileの
    # どちらでもvalid repositoryを確立できなければ、固定診断だけでfail-closeする。
    $expectedGitMetadataDiagnostic =
        'Private marker scan failed closed (integrity: git-probe).'
    foreach ($metadataKind in @('directory', 'file')) {
        $metadataRoot = Join-Path `
            $tempRoot `
            "invalid-git-metadata-$metadataKind"
        New-Item -ItemType Directory -Path $metadataRoot -Force | Out-Null
        $metadataPath = Join-Path $metadataRoot '.git'
        if ($metadataKind -eq 'directory') {
            New-Item `
                -ItemType Directory `
                -Path $metadataPath `
                -Force | Out-Null
        } else {
            [IO.File]::WriteAllText(
                $metadataPath,
                'gitdir: ../synthetic-missing-git-directory',
                [Text.UTF8Encoding]::new($false)
            )
        }
        Set-Content `
            -LiteralPath (Join-Path $metadataRoot 'README.md') `
            -Value 'synthetic clean content' `
            -Encoding UTF8
        $metadataResult = Invoke-Scanner -ScanPath $metadataRoot
        if ($metadataResult.ExitCode -ne 2 -or
            $metadataResult.Output.Trim() -cne
                $expectedGitMetadataDiagnostic) {
            Add-Failure "Expected invalid root-level .git $metadataKind metadata to fail closed with the fixed diagnostic. Output: $($metadataResult.Output.Trim())"
        }
    }

    # scan root自身にmetadataが無くても、親階層の`.git`をGit probe失敗後に
    # 見落としてはならない。directory/fileの両形をchild rootから固定する。
    foreach ($metadataKind in @('directory', 'file')) {
        $ancestorMetadataParent = Join-Path `
            $tempRoot `
            "invalid-ancestor-git-metadata-$metadataKind"
        $ancestorScanRoot = Join-Path $ancestorMetadataParent 'scan-root'
        New-Item `
            -ItemType Directory `
            -Path $ancestorScanRoot `
            -Force | Out-Null
        $ancestorMetadataPath = Join-Path $ancestorMetadataParent '.git'
        if ($metadataKind -eq 'directory') {
            New-Item `
                -ItemType Directory `
                -Path $ancestorMetadataPath `
                -Force | Out-Null
        } else {
            [IO.File]::WriteAllText(
                $ancestorMetadataPath,
                'gitdir: ../synthetic-missing-git-directory',
                [Text.UTF8Encoding]::new($false)
            )
        }
        Set-Content `
            -LiteralPath (Join-Path $ancestorScanRoot 'README.md') `
            -Value 'synthetic clean content' `
            -Encoding UTF8

        $ancestorMetadataResult = Invoke-Scanner `
            -ScanPath $ancestorScanRoot
        if ($ancestorMetadataResult.ExitCode -ne 2 -or
            $ancestorMetadataResult.Output.Trim() -cne
                $expectedGitMetadataDiagnostic) {
            Add-Failure "Expected invalid ancestor .git $metadataKind metadata to fail closed with the fixed diagnostic. Output: $($ancestorMetadataResult.Output.Trim())"
        }
    }

    # Higher-recall cloud / PEM prefixes, with one redaction regression each.
    # Fixtures are synthetic placeholders only; no real secrets are used.
    $prefixCases = @(
        @{ Rule = 'openai-api-key-prefix';            Marker = ('s' + 'k-') + 'SyntheticOpenAI000000000000' }
        @{ Rule = 'aws-access-key-id';                Marker = ('A' + 'KIA') + 'EXAMPLE0000000000000' }
        @{ Rule = 'gcp-api-key-prefix';               Marker = ('AIza') + 'Synthetic0000000000000000000000000000' }
        @{ Rule = 'slack-user-token-prefix';          Marker = ('xo' + 'xp-') + 'synthetic-placeholder' }
        @{ Rule = 'slack-legacy-app-token-prefix';    Marker = ('xo' + 'xa-') + 'synthetic-placeholder' }
        @{ Rule = 'slack-app-level-token-prefix';     Marker = ('xa' + 'pp-') + 'synthetic-placeholder' }
        @{ Rule = 'stripe-live-secret-key';           Marker = ('s' + 'k') + '_live_SyntheticPlaceholder0000' }
        @{ Rule = 'pem-private-key-block';            Marker = '-----' + ('BEGIN ' + 'OPENSSH PRIVATE KEY') + '-----' }
    )

    foreach ($case in $prefixCases) {
        $prefixRoot = Join-Path $tempRoot ('prefix-' + $case.Rule)
        New-Item -ItemType Directory -Path $prefixRoot | Out-Null
        Set-Content -LiteralPath (Join-Path $prefixRoot 'leak.txt') -Value "synthetic marker: $($case.Marker)" -Encoding UTF8

        $prefixResult = Invoke-Scanner -ScanPath $prefixRoot
        if ($prefixResult.ExitCode -eq 0) {
            Add-Failure "Expected $($case.Rule) fixture to fail, but scanner exited 0."
        }
        if ($prefixResult.Output -notmatch [regex]::Escape($case.Rule)) {
            Add-Failure "Expected output to name $($case.Rule). Output: $($prefixResult.Output.Trim())"
        }
        # Preserve redaction: the raw marker value must never appear in output.
        if ($prefixResult.Output.Contains($case.Marker)) {
            Add-Failure "Expected $($case.Rule) finding to be redacted, but the raw marker leaked into output."
        }
        if ($prefixResult.Output -notmatch '<redacted>') {
            Add-Failure "Expected $($case.Rule) finding to report '<redacted>'. Output: $($prefixResult.Output.Trim())"
        }
    }

    # windows-absolute-path: private-looking paths should be findings.
    # Split the literal so this test file does not make the scanner flag itself.
    $winPathRealRoot = Join-Path $tempRoot 'winpath-real'
    New-Item -ItemType Directory -Path $winPathRealRoot | Out-Null
    $realWinPath = 'C' + ':\Users\realperson\Secrets\config'
    Set-Content -LiteralPath (Join-Path $winPathRealRoot 'doc.md') -Value "See $realWinPath for details." -Encoding UTF8
    $winPathRealResult = Invoke-Scanner -ScanPath $winPathRealRoot
    if ($winPathRealResult.ExitCode -eq 0) {
        Add-Failure 'Expected real-looking Windows path fixture to fail, but scanner exited 0.'
    }
    if ($winPathRealResult.Output -notmatch 'windows-absolute-path') {
        Add-Failure "Expected real Windows path output to name windows-absolute-path. Output: $($winPathRealResult.Output.Trim())"
    }

    # windows-absolute-path: documented placeholders should not be findings.
    $winPathDocRoot = Join-Path $tempRoot 'winpath-doc'
    New-Item -ItemType Directory -Path $winPathDocRoot | Out-Null
    Set-Content -LiteralPath (Join-Path $winPathDocRoot 'doc.md') -Value @'
Use a placeholder path such as C:\path\to\repo in examples.
You can also write C:\Users\<name>\project to describe a user directory.
'@ -Encoding UTF8
    $winPathDocResult = Invoke-Scanner -ScanPath $winPathDocRoot
    if ($winPathDocResult.ExitCode -ne 0) {
        Add-Failure "Expected placeholder Windows path doc to pass, but scanner exited $($winPathDocResult.ExitCode): $($winPathDocResult.Output.Trim())"
    }

    # 034固有契約: このリポジトリ自身だけを許可し、別リポジトリは検出する。
    # fixture自体がURL検査へ混入しないよう、値は断片から合成する。
    $urlBase = 'https://' + 'github.com/'
    $allowedUrlRoot = Join-Path $tempRoot 'url-allowed'
    New-Item -ItemType Directory -Path $allowedUrlRoot | Out-Null
    $allowedUrl = $urlBase +
        'h8nc4y/markdown-idempotent-section-merge'
    Set-Content -LiteralPath (Join-Path $allowedUrlRoot 'doc.md') -Value "Related: $allowedUrl" -Encoding UTF8
    $allowedUrlResult = Invoke-Scanner -ScanPath $allowedUrlRoot
    if ($allowedUrlResult.ExitCode -ne 0) {
        Add-Failure "Expected the own repository URL to pass, but scanner exited $($allowedUrlResult.ExitCode): $($allowedUrlResult.Output.Trim())"
    }

    $foreignUrlRoot = Join-Path $tempRoot 'url-foreign'
    New-Item -ItemType Directory -Path $foreignUrlRoot | Out-Null
    $foreignUrl = $urlBase + 'h8nc4y/synthetic-other-repository'
    Set-Content -LiteralPath (Join-Path $foreignUrlRoot 'doc.md') -Value "See $foreignUrl" -Encoding UTF8
    $foreignUrlResult = Invoke-Scanner -ScanPath $foreignUrlRoot
    if ($foreignUrlResult.ExitCode -eq 0) {
        Add-Failure 'Expected non-allowlisted repository URL to fail, but scanner exited 0.'
    }
    if ($foreignUrlResult.Output -notmatch 'non-allowlisted-github-repo-url') {
        Add-Failure "Expected foreign URL output to name non-allowlisted-github-repo-url. Output: $($foreignUrlResult.Output.Trim())"
    }

    # A marker-dense line must retain a bounded useful report rather than
    # allocating or emitting one row for every URL.
    $denseUrlRoot = Join-Path $tempRoot 'url-dense'
    New-Item -ItemType Directory -Path $denseUrlRoot | Out-Null
    $denseUrls = 1..150 | ForEach-Object {
        $urlBase + "synthetic-owner/synthetic-repo-$_"
    }
    [IO.File]::WriteAllText(
        (Join-Path $denseUrlRoot 'dense.md'),
        ($denseUrls -join ' '),
        [Text.UTF8Encoding]::new($false)
    )
    $denseUrlResult = Invoke-Scanner -ScanPath $denseUrlRoot
    if ($denseUrlResult.ExitCode -eq 0 -or
        $denseUrlResult.OutputLimitExceeded -or
        $denseUrlResult.Output -notmatch
        'Additional findings omitted after 100 entries\.') {
        Add-Failure "Expected dense URL findings to fail with a bounded truncation notice. Output: $($denseUrlResult.Output.Trim())"
    }

    # Exercise production budget branches with lower constants in a disposable
    # scanner copy. This keeps the fixture fast while proving that zero-byte
    # files, non-text entries, line objects, and allowlisted regex matches all
    # have independent finite caps.
    $budgetScannerDirectory = Join-Path $tempRoot 'budget-scanner'
    New-Item `
        -ItemType Directory `
        -Path $budgetScannerDirectory `
        -Force | Out-Null
    $budgetScanner = Join-Path `
        $budgetScannerDirectory `
        'scan-private-markers.ps1'
    Copy-Item `
        -LiteralPath $processSupport `
        -Destination (Join-Path `
            $budgetScannerDirectory `
            'private-marker-process.ps1')
    $budgetScannerSource = [IO.File]::ReadAllText($scanner)
    $budgetReplacements = [ordered]@{
        '$maxScanTargets = 8192' = '$maxScanTargets = 4'
        '$maxWorkingTreeEntries = 32768' =
            '$maxWorkingTreeEntries = 5'
        '$maxScanLines = 1000000' = '$maxScanLines = 3'
        '$maxRegexMatches = 100000' = '$maxRegexMatches = 5'
        '$maxFindingOutputBytes = 16384' =
            '$maxFindingOutputBytes = 512'
        '$maxLocalMarkerBytes = 262144' =
            '$maxLocalMarkerBytes = 64'
        '$maxLocalMarkers = 256' = '$maxLocalMarkers = 2'
        '$maxLocalMarkerCharacters = 4096' =
            '$maxLocalMarkerCharacters = 8'
    }
    foreach ($budgetNeedle in $budgetReplacements.Keys) {
        if (-not $budgetScannerSource.Contains($budgetNeedle)) {
            throw "Cannot locate scanner budget constant: $budgetNeedle"
        }
        $budgetScannerSource = $budgetScannerSource.Replace(
            $budgetNeedle,
            $budgetReplacements[$budgetNeedle]
        )
    }
    [IO.File]::WriteAllText(
        $budgetScanner,
        $budgetScannerSource,
        # scanner本体はPS5.1でも日本語意図コメントを正しくparseできるようBOM付き。
        [Text.UTF8Encoding]::new($true)
    )
    $originalScanner = $scanner
    try {
        $scanner = $budgetScanner

        $zeroTargetRoot = Join-Path $tempRoot 'zero-target-budget'
        New-Item -ItemType Directory -Path $zeroTargetRoot | Out-Null
        1..5 | ForEach-Object {
            [IO.File]::WriteAllBytes(
                (Join-Path $zeroTargetRoot "empty-$_.md"),
                [byte[]]@()
            )
        }
        $zeroTargetResult = Invoke-Scanner -ScanPath $zeroTargetRoot
        if ($zeroTargetResult.ExitCode -ne 2 -or
            $zeroTargetResult.Output -notmatch
            'integrity: scan-target-count') {
            Add-Failure "Expected zero-byte target count to fail closed at its budget. Output: $($zeroTargetResult.Output.Trim())"
        }

        $entryBudgetRoot = Join-Path $tempRoot 'entry-budget'
        New-Item -ItemType Directory -Path $entryBudgetRoot | Out-Null
        1..6 | ForEach-Object {
            [IO.File]::WriteAllBytes(
                (Join-Path $entryBudgetRoot "binary-$_.bin"),
                [byte[]]@(0)
            )
        }
        $entryBudgetResult = Invoke-Scanner -ScanPath $entryBudgetRoot
        if ($entryBudgetResult.ExitCode -ne 2 -or
            $entryBudgetResult.Output -notmatch
            'integrity: working-tree-entry-budget') {
            Add-Failure "Expected fallback entry enumeration to fail closed at its budget. Output: $($entryBudgetResult.Output.Trim())"
        }

        $lineBudgetRoot = Join-Path $tempRoot 'line-budget'
        New-Item -ItemType Directory -Path $lineBudgetRoot | Out-Null
        [IO.File]::WriteAllText(
            (Join-Path $lineBudgetRoot 'lines.md'),
            "one`ntwo`nthree`nfour",
            [Text.UTF8Encoding]::new($false)
        )
        $lineBudgetResult = Invoke-Scanner -ScanPath $lineBudgetRoot
        if ($lineBudgetResult.ExitCode -ne 2 -or
            $lineBudgetResult.Output -notmatch
            'integrity: scan-line-budget') {
            Add-Failure "Expected streaming line scan to fail closed at its budget. Output: $($lineBudgetResult.Output.Trim())"
        }

        $regexBudgetRoot = Join-Path $tempRoot 'regex-budget'
        New-Item -ItemType Directory -Path $regexBudgetRoot | Out-Null
        $allowedBudgetUrl = $urlBase +
            'h8nc4y/markdown-idempotent-section-merge'
        [IO.File]::WriteAllText(
            (Join-Path $regexBudgetRoot 'urls.md'),
            ((1..6 | ForEach-Object { $allowedBudgetUrl }) -join ' '),
            [Text.UTF8Encoding]::new($false)
        )
        $regexBudgetResult = Invoke-Scanner -ScanPath $regexBudgetRoot
        if ($regexBudgetResult.ExitCode -ne 2 -or
            $regexBudgetResult.Output -notmatch
            'integrity: scan-regex-match-budget') {
            Add-Failure "Expected lazy regex matching to fail closed at its budget. Output: $($regexBudgetResult.Output.Trim())"
        }

        # 実OS newlineを含むreport payloadを512/513 bytesへ隣接させる。
        # cap内は全payloadを一度だけ返し、1 byte超過はpartial tableを
        # 一切返さず固定integrity codeへ縮退する。
        $findingPayloadPlan = {
            param(
                [int]$TargetBytes,
                [string]$FixtureRoot
            )

            $reportNewline = [Environment]::NewLine
            $reportPrefix =
                'Private marker scan failed (scan target: working-tree):'
            $reportHeader = "File`tLine`tRule`tMatch"
            $findingRule = 'github-classic-token-prefix'
            $findingMatch = '<redacted>'
            $baseBytes = [Text.Encoding]::UTF8.GetByteCount(
                $reportPrefix +
                $reportNewline +
                $reportHeader +
                $reportNewline
            )
            $firstLength = 0
            $secondLength = 220
            for ($candidateLength = 6;
                $candidateLength -le 220;
                $candidateLength++) {
                $candidateBytes = $baseBytes +
                    [Text.Encoding]::UTF8.GetByteCount(
                        ('x' * $candidateLength) +
                        "`t1`t$findingRule`t$findingMatch" +
                        $reportNewline
                    ) +
                    [Text.Encoding]::UTF8.GetByteCount(
                        ('y' * $secondLength) +
                        "`t1`t$findingRule`t$findingMatch" +
                        $reportNewline
                    )
                if ($candidateBytes -eq $TargetBytes) {
                    $firstLength = $candidateLength
                    break
                }
            }
            if ($firstLength -eq 0) {
                throw "Cannot construct $TargetBytes-byte finding payload."
            }

            New-Item -ItemType Directory -Path $FixtureRoot | Out-Null
            $firstName =
                'a-' + ('x' * ($firstLength - 6)) + '.txt'
            $secondName =
                'b-' + ('y' * ($secondLength - 6)) + '.txt'
            [IO.File]::WriteAllText(
                (Join-Path $FixtureRoot $firstName),
                ('g' + 'hp_') + 'finding_output_boundary',
                [Text.UTF8Encoding]::new($false)
            )
            [IO.File]::WriteAllText(
                (Join-Path $FixtureRoot $secondName),
                ('g' + 'hp_') + 'finding_output_boundary',
                [Text.UTF8Encoding]::new($false)
            )
        }

        $findingBoundaryRoot = Join-Path `
            $tempRoot `
            'finding-output-boundary'
        & $findingPayloadPlan 512 $findingBoundaryRoot
        $findingBoundaryResult = Invoke-Scanner `
            -ScanPath $findingBoundaryRoot
        if ($findingBoundaryResult.ExitCode -eq 0 -or
            $findingBoundaryResult.StdoutBytes.Length -ne 512 -or
            $findingBoundaryResult.StderrBytes.Length -ne 0 -or
            $findingBoundaryResult.Output -match
                'integrity: finding-output-budget') {
            Add-Failure 'Expected one exact 512-byte finding report including the actual OS newline.'
        }

        $findingOverRoot = Join-Path $tempRoot 'finding-output-over'
        & $findingPayloadPlan 513 $findingOverRoot
        $findingOverResult = Invoke-Scanner -ScanPath $findingOverRoot
        if ($findingOverResult.ExitCode -ne 2 -or
            $findingOverResult.Output -notmatch
                'integrity: finding-output-budget' -or
            $findingOverResult.Output -match "File`tLine`tRule`tMatch") {
            Add-Failure 'Expected a 513-byte finding report to fail closed without a partial table.'
        }

        $markerCountBudgetRoot = Join-Path `
            $tempRoot `
            'marker-count-budget'
        New-Item `
            -ItemType Directory `
            -Path $markerCountBudgetRoot | Out-Null
        $markerCountBudgetResult = Invoke-Scanner `
            -ScanPath $markerCountBudgetRoot `
            -InheritedEnvironment @{
                MARKDOWN_IDEMPOTENT_SECTION_MERGE_PRIVATE_MARKERS =
                    "one`ntwo`nthree"
            }
        if ($markerCountBudgetResult.ExitCode -ne 2 -or
            $markerCountBudgetResult.Output -notmatch
            'integrity: local-marker-count') {
            Add-Failure "Expected streaming environment markers to fail closed at their count budget. Output: $($markerCountBudgetResult.Output.Trim())"
        }

        $markerLengthBudgetRoot = Join-Path `
            $tempRoot `
            'marker-length-budget'
        New-Item `
            -ItemType Directory `
            -Path $markerLengthBudgetRoot | Out-Null
        $markerLengthBudgetResult = Invoke-Scanner `
            -ScanPath $markerLengthBudgetRoot `
            -InheritedEnvironment @{
                MARKDOWN_IDEMPOTENT_SECTION_MERGE_PRIVATE_MARKERS =
                    '123456789'
            }
        if ($markerLengthBudgetResult.ExitCode -ne 2 -or
            $markerLengthBudgetResult.Output -notmatch
            'integrity: local-marker-length') {
            Add-Failure "Expected one local marker to fail closed at its character budget. Output: $($markerLengthBudgetResult.Output.Trim())"
        }

        $markerSizeBudgetRoot = Join-Path `
            $tempRoot `
            'marker-size-budget'
        New-Item `
            -ItemType Directory `
            -Path $markerSizeBudgetRoot | Out-Null
        [IO.File]::WriteAllText(
            (Join-Path `
                $markerSizeBudgetRoot `
                '.private-markers.local'),
            ('x' * 65),
            [Text.UTF8Encoding]::new($false)
        )
        $markerSizeBudgetResult = Invoke-Scanner `
            -ScanPath $markerSizeBudgetRoot
        if ($markerSizeBudgetResult.ExitCode -ne 2 -or
            $markerSizeBudgetResult.Output -notmatch
            'integrity: local-marker-type') {
            Add-Failure "Expected local marker bytes to fail closed at their size budget. Output: $($markerSizeBudgetResult.Output.Trim())"
        }
    }
    finally {
        $scanner = $originalScanner
    }

    # Unicode format and logical line-separator characters can reorder or forge
    # log text even though Char.IsControl does not classify them as controls.
    $unicodePathRoot = Join-Path $tempRoot 'unicode-display-path'
    New-Item -ItemType Directory -Path $unicodePathRoot | Out-Null
    $unicodePathName = 'format' + [char]0x202E +
        'line' + [char]0x2028 + 'name.md'
    $unicodePathMarker = ('g' + 'hp_') +
        'synthetic_unicode_display'
    [IO.File]::WriteAllText(
        (Join-Path $unicodePathRoot $unicodePathName),
        "synthetic marker: $unicodePathMarker",
        [Text.UTF8Encoding]::new($false)
    )
    $unicodePathResult = Invoke-Scanner -ScanPath $unicodePathRoot
    if ($unicodePathResult.ExitCode -eq 0 -or
        $unicodePathResult.Output -notmatch
        'format\\u202eline\\u2028name\.md' -or
        $unicodePathResult.Output.Contains([string][char]0x202E) -or
        $unicodePathResult.Output.Contains([string][char]0x2028)) {
        Add-Failure "Expected Unicode format/separator characters in displayed paths to be escaped. Output: $($unicodePathResult.Output.Trim())"
    }

    $localMarkerRoot = Join-Path $tempRoot 'local-marker'
    New-Item -ItemType Directory -Path $localMarkerRoot | Out-Null
    Set-Content -LiteralPath (Join-Path $localMarkerRoot '.private-markers.local') -Value 'local-only-marker' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $localMarkerRoot 'leak.txt') -Value 'synthetic local-only-marker fixture' -Encoding UTF8

    $localMarkerResult = Invoke-Scanner -ScanPath $localMarkerRoot
    if ($localMarkerResult.ExitCode -eq 0) {
        Add-Failure 'Expected local marker fixture to fail, but scanner exited 0.'
    }
    if ($localMarkerResult.Output -notmatch 'local-private-marker-1') {
        Add-Failure "Expected local marker output to name local-private-marker-1. Output: $($localMarkerResult.Output.Trim())"
    }

    # The scan root itself must remain a real directory. Ancestor aliases are
    # tolerated only after this leaf-level link/reparse gate has passed.
    $linkedRootTarget = Join-Path $tempRoot 'linked-root-target'
    $linkedScanRoot = Join-Path $tempRoot 'linked-scan-root'
    New-Item -ItemType Directory -Path $linkedRootTarget | Out-Null
    try {
        if ($runtimeIsWindows) {
            New-Item `
                -ItemType Junction `
                -Path $linkedScanRoot `
                -Target $linkedRootTarget |
                Out-Null
        } else {
            New-Item `
                -ItemType SymbolicLink `
                -Path $linkedScanRoot `
                -Target $linkedRootTarget |
                Out-Null
        }
        $linkedRootResult = Invoke-Scanner -ScanPath $linkedScanRoot
        if ($linkedRootResult.ExitCode -ne 2 -or
            $linkedRootResult.Output -notmatch
            'integrity: scan-root-type') {
            Add-Failure "Expected a linked scan root to fail closed. Output: $($linkedRootResult.Output.Trim())"
        }
    }
    catch {
        Add-Failure 'Expected the linked scan-root fixture to be creatable.'
    }

    # Gitのforward-slash pathを全OSで保持し、nested fileとdotfileを同時に走査する。
    # Windows限定separator変換とUnix側`-Force`漏れを同じfixtureで検出する。
    $gitCommand = Get-Command git -ErrorAction SilentlyContinue
    if ($null -ne $gitCommand) {
        $gitRoot = Join-Path $tempRoot 'git-tracked'
        $gitIsolationRoot = Join-Path $tempRoot 'git-isolation'
        $ambientHooksRoot = Join-Path $tempRoot 'ambient-hooks'
        $ambientFilterRoot = Join-Path $tempRoot 'ambient-filter'
        $ambientGitDirectory = Join-Path $tempRoot 'ambient-git-dir'
        $ambientWorkTree = Join-Path $tempRoot 'ambient-work-tree'
        $ambientObjectDirectory = Join-Path $tempRoot 'ambient-objects'
        $ambientIndexFile = Join-Path $tempRoot 'ambient-index'
        $ambientHome = Join-Path $tempRoot 'ambient-home'
        $ambientXdg = Join-Path $tempRoot 'ambient-xdg'
        $ambientExecPath = Join-Path $tempRoot 'ambient-git-exec'
        $hookSentinel = Join-Path $tempRoot 'ambient-hook-fired'
        $filterSentinel = Join-Path $tempRoot 'ambient-filter-fired'
        $traceSentinel = Join-Path $tempRoot 'ambient-trace-fired'
        foreach ($directory in @(
            $gitRoot,
            $ambientHooksRoot,
            $ambientFilterRoot,
            $ambientGitDirectory,
            $ambientWorkTree,
            $ambientObjectDirectory,
            $ambientHome,
            $ambientXdg,
            $ambientExecPath
        )) {
            New-Item -ItemType Directory -Path $directory | Out-Null
        }

        # Inject harmless ambient hook/filter commands and repository redirects
        # as an adversarial regression. The isolated wrapper must suppress all
        # of them, and must restore the caller's exact environment afterward.
        $hookPath = Join-Path $ambientHooksRoot 'post-index-change'
        $hookContent = @'
#!/bin/sh
printf '%s\n' 'hook-fired' > "$MARKDOWN_MERGE_TEST_HOOK_SENTINEL"
'@
        [System.IO.File]::WriteAllText($hookPath, $hookContent, [System.Text.UTF8Encoding]::new($false))
        $filterPath = Join-Path $ambientFilterRoot 'clean-filter'
        $filterContent = @'
#!/bin/sh
printf '%s\n' 'filter-fired' > "$MARKDOWN_MERGE_TEST_FILTER_SENTINEL"
cat
'@
        [System.IO.File]::WriteAllText($filterPath, $filterContent, [System.Text.UTF8Encoding]::new($false))
        $ambientAttributes = Join-Path $ambientFilterRoot 'global-attributes'
        [System.IO.File]::WriteAllText(
            $ambientAttributes,
            "*.md filter=handoff-test`n",
            [System.Text.UTF8Encoding]::new($false)
        )

        $chmodCommand = Get-Command chmod -ErrorAction SilentlyContinue
        if ($null -ne $chmodCommand) {
            $chmodIsolationRoot = Join-Path $tempRoot 'chmod-isolation'
            $chmodResult = Invoke-PrivateMarkerBoundedProcess `
                -FileName $chmodCommand.Source `
                -Arguments @('+x', $hookPath, $filterPath) `
                -IsolationRoot $chmodIsolationRoot `
                -TimeoutMilliseconds 10000
            if ($chmodResult.ExitCode -ne 0 -or
                $chmodResult.TimedOut -or
                -not $chmodResult.TreeStopped -or
                -not $chmodResult.StreamsDrained) {
                Add-Failure "Expected bounded synthetic hook/filter chmod to exit 0. Output: $($chmodResult.Output.Trim())"
            }
        }

        $beforeFixtureEnvironment = Get-ProcessEnvironmentSnapshot
        $ambientHooksGitPath = $ambientHooksRoot.Replace([string][char]92, '/')
        $ambientAttributesGitPath = $ambientAttributes.Replace([string][char]92, '/')
        $filterGitPath = $filterPath.Replace([string][char]92, '/')
        $adversarialEnvironment = @{
            GIT_CONFIG_COUNT = '4'
            GIT_CONFIG_KEY_0 = 'core.hooksPath'
            GIT_CONFIG_VALUE_0 = $ambientHooksGitPath
            GIT_CONFIG_KEY_1 = 'core.attributesFile'
            GIT_CONFIG_VALUE_1 = $ambientAttributesGitPath
            GIT_CONFIG_KEY_2 = 'filter.handoff-test.clean'
            GIT_CONFIG_VALUE_2 = "sh `"$filterGitPath`""
            GIT_CONFIG_KEY_3 = 'filter.handoff-test.required'
            GIT_CONFIG_VALUE_3 = 'true'
            GIT_CONFIG_NOSYSTEM = '0'
            GIT_ATTR_NOSYSTEM = '0'
            GIT_CONFIG_GLOBAL = $ambientAttributesGitPath
            GIT_CONFIG_SYSTEM = $ambientAttributesGitPath
            GIT_DIR = $ambientGitDirectory
            GIT_WORK_TREE = $ambientWorkTree
            GIT_INDEX_FILE = $ambientIndexFile
            GIT_OBJECT_DIRECTORY = $ambientObjectDirectory
            GIT_ALTERNATE_OBJECT_DIRECTORIES = $ambientObjectDirectory
            GIT_EXEC_PATH = $ambientExecPath
            GIT_TRACE2_EVENT = $traceSentinel.Replace([string][char]92, '/')
            GIT_NO_REPLACE_OBJECTS = '0'
            GIT_NO_LAZY_FETCH = '0'
            GIT_MARKDOWN_MERGE_PRESENT_EMPTY = ''
            MARKDOWN_MERGE_TEST_HOOK_SENTINEL = $hookSentinel
            MARKDOWN_MERGE_TEST_FILTER_SENTINEL = $filterSentinel
            HOME = $ambientHome
            USERPROFILE = $ambientHome
            XDG_CONFIG_HOME = $ambientXdg
        }

        function Invoke-CheckedFixtureGit {
            param(
                [string]$FixtureRoot,
                [string[]]$Arguments,
                [string]$Context
            )

            $result = Invoke-IsolatedGit `
                -GitPath $gitCommand.Source `
                -WorkingDirectory $FixtureRoot `
                -IsolationRoot $gitIsolationRoot `
                -Arguments $Arguments `
                -InheritedEnvironment $adversarialEnvironment
            if ($result.ExitCode -ne 0 -or
                -not (Test-BoundedResultHealthy -Result $result)) {
                Add-Failure "$Context failed with exit $($result.ExitCode). Output: $($result.Output.Trim())"
            }
            return $result
        }

        function New-CheckedGitFixture {
            param([string]$Name)

            $fixtureRoot = Join-Path $tempRoot $Name
            New-Item -ItemType Directory -Path $fixtureRoot -Force | Out-Null
            [void](Invoke-CheckedFixtureGit `
                -FixtureRoot $fixtureRoot `
                -Arguments @('init', '--quiet') `
                -Context "Initialize $Name")
            return $fixtureRoot
        }

        $gitInitResult = Invoke-IsolatedGit `
            -GitPath $gitCommand.Source `
            -WorkingDirectory $gitRoot `
            -IsolationRoot $gitIsolationRoot `
            -Arguments @('init', '--quiet') `
            -InheritedEnvironment $adversarialEnvironment
        if ($gitInitResult.ExitCode -ne 0 -or
            $gitInitResult.TimedOut -or
            -not $gitInitResult.TreeStopped -or
            -not $gitInitResult.StreamsDrained) {
            Add-Failure "Expected isolated git init to exit 0. Output: $($gitInitResult.Output.Trim())"
        }
        Assert-ProcessEnvironmentUnchanged `
            -Expected $beforeFixtureEnvironment `
            -Context 'Isolated git init'

        # Preserve a valid clean alternate index. A later public-entrypoint
        # regression passes only GIT_INDEX_FILE; if the scanner trusts ambient
        # Git state, the real staged markers disappear and the test false-passes.
        $emptyIndexResult = Invoke-IsolatedGit `
            -GitPath $gitCommand.Source `
            -WorkingDirectory $gitRoot `
            -IsolationRoot $gitIsolationRoot `
            -Arguments @('read-tree', '--empty') `
            -InheritedEnvironment $adversarialEnvironment
        if ($emptyIndexResult.ExitCode -ne 0 -or
            -not (Test-BoundedResultHealthy -Result $emptyIndexResult)) {
            Add-Failure "Expected isolated empty index creation to exit 0. Output: $($emptyIndexResult.Output.Trim())"
        } else {
            Copy-Item `
                -LiteralPath (Join-Path $gitRoot '.git/index') `
                -Destination $ambientIndexFile `
                -Force
        }

        $nestedDirectory = Join-Path $gitRoot (Join-Path 'sub' 'deep')
        New-Item -ItemType Directory -Path $nestedDirectory -Force | Out-Null
        $nestedMarker = ('g' + 'hp_') + 'synthetic_nested_placeholder'
        Set-Content -LiteralPath (Join-Path $nestedDirectory 'leak.md') -Value "synthetic marker: $nestedMarker" -Encoding UTF8

        $dotfileMarker = ('xo' + 'xb-') + 'synthetic_dotfile_placeholder'
        Set-Content -LiteralPath (Join-Path $gitRoot '.editorconfig') -Value "synthetic marker: $dotfileMarker" -Encoding UTF8

        $trackedTextNames = @(
            'leak.env',
            'leak.pem',
            'leak.key',
            '.env',
            '.env.local',
            'NOTICE',
            '.hidden'
        )
        foreach ($trackedTextName in $trackedTextNames) {
            Set-Content `
                -LiteralPath (Join-Path $gitRoot $trackedTextName) `
                -Value "synthetic marker: $nestedMarker" `
                -Encoding UTF8
        }

        $gitAddResult = Invoke-IsolatedGit `
            -GitPath $gitCommand.Source `
            -WorkingDirectory $gitRoot `
            -IsolationRoot $gitIsolationRoot `
            -Arguments @('add', '-A') `
            -InheritedEnvironment $adversarialEnvironment
        if ($gitAddResult.ExitCode -ne 0 -or
            $gitAddResult.TimedOut -or
            -not $gitAddResult.TreeStopped -or
            -not $gitAddResult.StreamsDrained) {
            Add-Failure "Expected isolated git add to exit 0. Output: $($gitAddResult.Output.Trim())"
        }
        Assert-ProcessEnvironmentUnchanged `
            -Expected $beforeFixtureEnvironment `
            -Context 'Isolated git add'

        # The scanner's own Git probes receive the same hostile clone and must
        # still inspect only the explicit temp repository.
        $gitResult = Invoke-Scanner `
            -ScanPath $gitRoot `
            -InheritedEnvironment $adversarialEnvironment
        Assert-ProcessEnvironmentUnchanged `
            -Expected $beforeFixtureEnvironment `
            -Context 'Isolated scanner child'

        if (Test-Path -LiteralPath $hookSentinel) {
            Add-Failure 'Expected isolated git fixture to suppress the injected ambient hook, but its sentinel was created.'
        }
        if (Test-Path -LiteralPath $filterSentinel) {
            Add-Failure 'Expected isolated git fixture to suppress the injected ambient clean filter, but its sentinel was created.'
        }
        if (Test-Path -LiteralPath $traceSentinel) {
            Add-Failure 'Expected isolated git fixture to suppress the injected trace output, but its sentinel was created.'
        }

        if ($null -eq $gitResult) {
            Add-Failure 'Expected the isolated scanner child to return a result.'
        } else {
            if ($gitResult.Output -notmatch 'git-index\+worktree') {
                Add-Failure "Expected the git fixture to scan the index and worktree. Output: $($gitResult.Output.Trim())"
            }
            if ($gitResult.ExitCode -eq 0) {
                Add-Failure 'Expected git-tracked nested and dotfile markers to fail the scan, but scanner exited 0.'
            }
            if ($gitResult.Output -notmatch 'sub/deep/leak\.md') {
                Add-Failure "Expected git-tracked output to list sub/deep/leak.md. Output: $($gitResult.Output.Trim())"
            }
            if ($gitResult.Output -notmatch '\.editorconfig') {
                Add-Failure "Expected git-tracked output to list .editorconfig. Output: $($gitResult.Output.Trim())"
            }
            foreach ($trackedTextName in $trackedTextNames) {
                if ($gitResult.Output -notmatch
                    [regex]::Escape($trackedTextName)) {
                    Add-Failure "Expected tracked regular text path $trackedTextName to be scanned. Output: $($gitResult.Output.Trim())"
                }
            }
            if ($gitResult.Output.Contains($nestedMarker) -or
                $gitResult.Output.Contains($dotfileMarker)) {
                Add-Failure 'Expected git-tracked findings to stay redacted, but a raw marker leaked into output.'
            }
        }

        # GIT_INDEX_FILE alone is a direct false-pass regression: the alternate
        # index is valid and empty, while the explicit repository index contains
        # both markers. The public scanner must ignore the ambient redirect.
        $ambientIndexOnlyResult = Invoke-Scanner `
            -ScanPath $gitRoot `
            -InheritedEnvironment @{
                GIT_INDEX_FILE = $ambientIndexFile
            }
        if ($ambientIndexOnlyResult.ExitCode -eq 0 -or
            $ambientIndexOnlyResult.Output -notmatch 'sub/deep/leak\.md' -or
            $ambientIndexOnlyResult.Output -notmatch '\.editorconfig') {
            Add-Failure "Expected the public scanner to ignore ambient GIT_INDEX_FILE. Output: $($ambientIndexOnlyResult.Output.Trim())"
        }

        if ($runtimeIsWindows -and
            $PSVersionTable.PSVersion.Major -ge 7 -and
            $gitRoot -match '^[A-Za-z]:\\') {
            # PowerShell 7 accepts Windows extended-length syntax as a second
            # lexical spelling of the same directory. It reproduces the macOS
            # /var -> /private/var mismatch without weakening the direct-link
            # root rejection. Windows PowerShell 5.1 cannot Join-Path this
            # syntax, while native macOS coverage also runs PowerShell 7.
            $extendedGitRoot = '\\?\' + $gitRoot
            $extendedRootResult = Invoke-Scanner -ScanPath $extendedGitRoot
            if ($extendedRootResult.ExitCode -ne 1 -or
                $extendedRootResult.Output -notmatch
                'scan target: git-index\+worktree' -or
                $extendedRootResult.Output -notmatch 'sub/deep/leak\.md' -or
                $extendedRootResult.Output -notmatch '\.editorconfig' -or
                $extendedRootResult.Output -match 'integrity:') {
                Add-Failure "Expected an equivalent extended-length repository root to scan normally. Output: $($extendedRootResult.Output.Trim())"
            }
        }

        # Passing a repository subdirectory as the scan root must fail closed.
        # It must never fall back to a partial working-tree scan.
        $rootMismatchResult = Invoke-Scanner -ScanPath (Join-Path $gitRoot 'sub')
        if ($rootMismatchResult.ExitCode -ne 2 -or
            $rootMismatchResult.Output -notmatch 'integrity: git-root-mismatch') {
            Add-Failure "Expected repository root mismatch to fail closed. Output: $($rootMismatchResult.Output.Trim())"
        }

        # Git metadata and a bare repository can both report an empty prefix,
        # but neither is a worktree root. The inside-worktree record is
        # therefore part of the same fail-closed identity contract.
        $gitDirectoryResult = Invoke-Scanner `
            -ScanPath (Join-Path $gitRoot '.git')
        if ($gitDirectoryResult.ExitCode -ne 2 -or
            $gitDirectoryResult.Output -notmatch
            'integrity: git-root-mismatch') {
            Add-Failure "Expected the Git directory itself to fail closed. Output: $($gitDirectoryResult.Output.Trim())"
        }

        $bareRoot = Join-Path $tempRoot 'bare-repository'
        New-Item -ItemType Directory -Path $bareRoot | Out-Null
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $bareRoot `
            -Arguments @('init', '--quiet', '--bare') `
            -Context 'Initialize bare repository')
        $bareRootResult = Invoke-Scanner -ScanPath $bareRoot
        if ($bareRootResult.ExitCode -ne 2 -or
            $bareRootResult.Output -notmatch
            'integrity: git-root-mismatch') {
            Add-Failure "Expected a bare repository to fail closed. Output: $($bareRootResult.Output.Trim())"
        }

        # Normalize the original fixture before exercising index/worktree union
        # states with one dedicated path.
        Set-Content `
            -LiteralPath (Join-Path $nestedDirectory 'leak.md') `
            -Value 'synthetic clean content' `
            -Encoding UTF8
        Set-Content `
            -LiteralPath (Join-Path $gitRoot '.editorconfig') `
            -Value 'root = true' `
            -Encoding UTF8
        foreach ($trackedTextName in $trackedTextNames) {
            Set-Content `
                -LiteralPath (Join-Path $gitRoot $trackedTextName) `
                -Value 'synthetic clean content' `
                -Encoding UTF8
        }
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $gitRoot `
            -Arguments @('add', '-A') `
            -Context 'Stage normalized union fixture')

        $unionPath = Join-Path $gitRoot 'union.md'
        $unionMarker = ('g' + 'hp_') + 'synthetic_union_placeholder'
        Set-Content `
            -LiteralPath $unionPath `
            -Value "staged marker: $unionMarker" `
            -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $gitRoot `
            -Arguments @('add', '--', 'union.md') `
            -Context 'Stage index-only marker')
        Set-Content `
            -LiteralPath $unionPath `
            -Value 'synthetic clean worktree content' `
            -Encoding UTF8
        $indexOnlyResult = Invoke-Scanner -ScanPath $gitRoot
        if ($indexOnlyResult.ExitCode -eq 0 -or
            $indexOnlyResult.Output -notmatch 'union\.md \[index\]') {
            Add-Failure "Expected staged-only index bytes to be scanned. Output: $($indexOnlyResult.Output.Trim())"
        }

        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $gitRoot `
            -Arguments @('add', '--', 'union.md') `
            -Context 'Stage clean union content')
        Set-Content `
            -LiteralPath $unionPath `
            -Value "worktree marker: $unionMarker" `
            -Encoding UTF8
        $worktreeOnlyResult = Invoke-Scanner -ScanPath $gitRoot
        if ($worktreeOnlyResult.ExitCode -eq 0 -or
            $worktreeOnlyResult.Output -notmatch 'union\.md \[worktree\]') {
            Add-Failure "Expected unstaged worktree bytes to be scanned. Output: $($worktreeOnlyResult.Output.Trim())"
        }

        Set-Content `
            -LiteralPath $unionPath `
            -Value "missing worktree marker: $unionMarker" `
            -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $gitRoot `
            -Arguments @('add', '--', 'union.md') `
            -Context 'Stage missing-worktree marker')
        Remove-Item -LiteralPath $unionPath -Force
        $missingWorktreeResult = Invoke-Scanner -ScanPath $gitRoot
        if ($missingWorktreeResult.ExitCode -eq 0 -or
            $missingWorktreeResult.Output -notmatch
            'union\.md \[index; worktree missing\]') {
            Add-Failure "Expected a missing worktree file to retain index scanning. Output: $($missingWorktreeResult.Output.Trim())"
        }

        # Replace the real index atomically after the first raw stage listing
        # has completed. A test-only scanner copy pauses at that exact boundary
        # so the fixture proves the final byte comparison without scheduler
        # timing assumptions or production-only sleeps.
        $driftRoot = New-CheckedGitFixture -Name 'index-drift'
        for ($driftFileIndex = 0; $driftFileIndex -lt 24; $driftFileIndex++) {
            $driftFileName = 'drift-{0:d2}.md' -f $driftFileIndex
            $driftContent = ('x' * 32768) + "-$driftFileIndex"
            [IO.File]::WriteAllText(
                (Join-Path $driftRoot $driftFileName),
                $driftContent,
                [Text.UTF8Encoding]::new($false)
            )
        }
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $driftRoot `
            -Arguments @('add', '-A') `
            -Context 'Stage clean drift index')
        $driftIndexPath = Join-Path $driftRoot '.git/index'
        $cleanIndexTemplate = Join-Path $driftRoot '.git/clean-index-template'
        $changedIndexTemplate = Join-Path `
            $driftRoot `
            '.git/changed-index-template'
        [IO.File]::Copy($driftIndexPath, $cleanIndexTemplate, $true)
        $driftMarker = ('g' + 'hp_') + 'synthetic_index_drift'
        [IO.File]::WriteAllText(
            (Join-Path $driftRoot 'drift-00.md'),
            "mutated index marker: $driftMarker",
            [Text.UTF8Encoding]::new($false)
        )
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $driftRoot `
            -Arguments @('add', '--', 'drift-00.md') `
            -Context 'Stage changed drift index')
        [IO.File]::Copy($driftIndexPath, $changedIndexTemplate, $true)
        [IO.File]::WriteAllText(
            (Join-Path $driftRoot 'drift-00.md'),
            ('x' * 32768) + '-0',
            [Text.UTF8Encoding]::new($false)
        )

        [IO.File]::Copy($cleanIndexTemplate, $driftIndexPath, $true)
        $swapIndexPath = Join-Path $driftRoot '.git/index-swap'
        $backupIndexPath = Join-Path $driftRoot '.git/index-backup'
        [IO.File]::Copy($changedIndexTemplate, $swapIndexPath, $true)
        if ([IO.File]::Exists($backupIndexPath)) {
            [IO.File]::Delete($backupIndexPath)
        }

        # Instrument only a disposable scanner copy. The production scanner
        # has no synchronization hooks or caller-controlled delay surface.
        $driftScannerDirectory = Join-Path $tempRoot 'drift-scanner'
        New-Item `
            -ItemType Directory `
            -Path $driftScannerDirectory `
            -Force | Out-Null
        $instrumentedScanner = Join-Path `
            $driftScannerDirectory `
            'scan-private-markers.ps1'
        Copy-Item `
            -LiteralPath $processSupport `
            -Destination (Join-Path `
                $driftScannerDirectory `
                'private-marker-process.ps1')
        $driftReadyPath = Join-Path $tempRoot 'index-drift-ready'
        $driftReleasePath = Join-Path $tempRoot 'index-drift-release'
        $driftAnchor = @'
if ($usingGitIndex) {
    $scanMode = 'git-index+worktree'
'@
        $driftScannerSource = [IO.File]::ReadAllText($scanner)
        $driftAnchorOffset = $driftScannerSource.IndexOf(
            $driftAnchor,
            [StringComparison]::Ordinal
        )
        if ($driftAnchorOffset -lt 0 -or
            $driftScannerSource.IndexOf(
                $driftAnchor,
                $driftAnchorOffset + $driftAnchor.Length,
                [StringComparison]::Ordinal
            ) -ge 0) {
            throw 'Cannot locate the unique index-drift synchronization anchor.'
        }
        $driftSyncInjection = @'
# Test-copy only: publish that snapshot construction and its first raw index
# recheck are complete, then pause before content matching. The production
# scanner has no synchronization surface.
[IO.File]::WriteAllText(
    '__DRIFT_READY__',
    'ready',
    [Text.UTF8Encoding]::new($false)
)
$driftReleased = $false
for ($driftSyncAttempt = 0;
    $driftSyncAttempt -lt 1500;
    $driftSyncAttempt++) {
    if ([IO.File]::Exists('__DRIFT_RELEASE__')) {
        $driftReleased = $true
        break
    }
    Start-Sleep -Milliseconds 10
}
if (-not $driftReleased) {
    Stop-ScanIntegrityFailure -Reason 'test-index-drift-sync'
}

'@
        $driftSyncInjection = $driftSyncInjection.Replace(
            '__DRIFT_READY__',
            $driftReadyPath.Replace("'", "''")
        ).Replace(
            '__DRIFT_RELEASE__',
            $driftReleasePath.Replace("'", "''")
        )
        $driftScannerSource = $driftScannerSource.Replace(
            $driftAnchor,
            $driftSyncInjection + $driftAnchor
        )
        [IO.File]::WriteAllText(
            $instrumentedScanner,
            $driftScannerSource,
            # instrumentation copyも本体と同じPS5.1 encoding契約を維持する。
            [Text.UTF8Encoding]::new($true)
        )

        # The mutator starts first but cannot replace the index until the
        # instrumented scanner publishes its post-snapshot signal.
        $escapedDriftReady = $driftReadyPath.Replace("'", "''")
        $escapedDriftRelease = $driftReleasePath.Replace("'", "''")
        $escapedSwapIndex = $swapIndexPath.Replace("'", "''")
        $escapedDriftIndex = $driftIndexPath.Replace("'", "''")
        $escapedBackupIndex = $backupIndexPath.Replace("'", "''")
        $mutatorScript = @"
`$readyObserved = `$false
for (`$attempt = 0; `$attempt -lt 1500; `$attempt++) {
    if ([IO.File]::Exists('$escapedDriftReady')) {
        `$readyObserved = `$true
        break
    }
    Start-Sleep -Milliseconds 10
}
if (-not `$readyObserved) {
    exit 3
}
[IO.File]::Replace(
    '$escapedSwapIndex',
    '$escapedDriftIndex',
    '$escapedBackupIndex'
)
[IO.File]::WriteAllText(
    '$escapedDriftRelease',
    'release',
    [Text.UTF8Encoding]::new(`$false)
)
"@
        $mutatorEncoded = [Convert]::ToBase64String(
            [Text.Encoding]::Unicode.GetBytes($mutatorScript)
        )
        $mutatorArguments = @('-NoProfile')
        if ($PSVersionTable.PSVersion.Major -le 5 -and
            $runtimeIsWindows) {
            $mutatorArguments += @('-ExecutionPolicy', 'Bypass')
        }
        $mutatorArguments += @('-EncodedCommand', $mutatorEncoded)
        $mutatorInfo = New-Object Diagnostics.ProcessStartInfo
        $mutatorInfo.FileName = $currentPowerShellExecutable
        $mutatorInfo.UseShellExecute = $false
        $mutatorInfo.CreateNoWindow = $true
        $mutatorArgumentList =
            $mutatorInfo.PSObject.Properties['ArgumentList']
        if ($null -ne $mutatorArgumentList) {
            foreach ($mutatorArgument in $mutatorArguments) {
                $mutatorInfo.ArgumentList.Add($mutatorArgument)
            }
        } else {
            $mutatorInfo.Arguments = (
                $mutatorArguments | ForEach-Object {
                    ConvertTo-PrivateMarkerProcessArgument -Argument $_
                }
            ) -join ' '
        }
        $mutator = [Diagnostics.Process]::Start($mutatorInfo)
        $originalScanner = $scanner
        try {
            $scanner = $instrumentedScanner
            $driftResult = Invoke-Scanner -ScanPath $driftRoot
            if (-not $mutator.WaitForExit(20000)) {
                $mutator.Kill()
                [void]$mutator.WaitForExit(5000)
                Add-Failure 'Expected the bounded index mutator to exit.'
            } elseif ($mutator.ExitCode -ne 0) {
                Add-Failure 'Expected the atomic index mutation to succeed.'
            }
        }
        finally {
            $scanner = $originalScanner
            $mutator.Dispose()
        }
        [IO.File]::Copy($cleanIndexTemplate, $driftIndexPath, $true)
        if ($driftResult.ExitCode -ne 2 -or
            $driftResult.Output -notmatch 'integrity: git-index-drift') {
            Add-Failure 'Expected an actual staged-index mutation to be caught by the final raw metadata comparison.'
        }

        # CE_INTENT_TO_ADD can change while mode/OID/stage/path bytes remain
        # identical. Swap an actual normal empty-file index for its ITA form
        # after the first stage recheck; only the final raw debug comparison can
        # detect this flags-only mutation.
        $flagDriftRoot = New-CheckedGitFixture -Name 'index-flag-drift'
        $flagDriftWorktreePath = Join-Path $flagDriftRoot 'intent.md'
        [IO.File]::WriteAllBytes($flagDriftWorktreePath, [byte[]]@())
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $flagDriftRoot `
            -Arguments @('add', '--', 'intent.md') `
            -Context 'Stage normal empty flag-drift entry')
        $flagDriftIndexPath = Join-Path $flagDriftRoot '.git/index'
        $normalFlagIndexTemplate = Join-Path `
            $flagDriftRoot `
            '.git/normal-flag-index-template'
        $intentFlagIndexTemplate = Join-Path `
            $flagDriftRoot `
            '.git/intent-flag-index-template'
        [IO.File]::Copy(
            $flagDriftIndexPath,
            $normalFlagIndexTemplate,
            $true
        )
        $normalFlagStage = Invoke-CheckedFixtureGit `
            -FixtureRoot $flagDriftRoot `
            -Arguments @('ls-files', '-z', '--stage', '--') `
            -Context 'Read normal flag-drift stage bytes'
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $flagDriftRoot `
            -Arguments @('rm', '--cached', '--', 'intent.md') `
            -Context 'Remove normal empty entry before ITA')
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $flagDriftRoot `
            -Arguments @('add', '--intent-to-add', '--', 'intent.md') `
            -Context 'Create flags-only ITA entry')
        $intentFlagStage = Invoke-CheckedFixtureGit `
            -FixtureRoot $flagDriftRoot `
            -Arguments @('ls-files', '-z', '--stage', '--') `
            -Context 'Read ITA flag-drift stage bytes'
        if ([Convert]::ToBase64String($normalFlagStage.StdoutBytes) -cne
            [Convert]::ToBase64String($intentFlagStage.StdoutBytes)) {
            Add-Failure 'Expected normal empty and ITA stage metadata bytes to be identical for the flags-only drift fixture.'
        }
        [IO.File]::Copy(
            $flagDriftIndexPath,
            $intentFlagIndexTemplate,
            $true
        )
        [IO.File]::Copy(
            $normalFlagIndexTemplate,
            $flagDriftIndexPath,
            $true
        )
        $flagSwapIndexPath = Join-Path `
            $flagDriftRoot `
            '.git/index-flag-swap'
        $flagBackupIndexPath = Join-Path `
            $flagDriftRoot `
            '.git/index-flag-backup'
        [IO.File]::Copy(
            $intentFlagIndexTemplate,
            $flagSwapIndexPath,
            $true
        )
        foreach ($signalPath in @(
            $driftReadyPath,
            $driftReleasePath,
            $flagBackupIndexPath
        )) {
            [IO.File]::Delete($signalPath)
        }
        $flagMutator = Start-SynchronizedIndexMutator `
            -ReadyPath $driftReadyPath `
            -ReleasePath $driftReleasePath `
            -SwapPath $flagSwapIndexPath `
            -IndexPath $flagDriftIndexPath `
            -BackupPath $flagBackupIndexPath
        $originalScanner = $scanner
        try {
            $scanner = $instrumentedScanner
            $flagDriftResult = Invoke-Scanner -ScanPath $flagDriftRoot
            if (-not $flagMutator.WaitForExit(20000)) {
                $flagMutator.Kill()
                [void]$flagMutator.WaitForExit(5000)
                Add-Failure 'Expected the bounded flag mutator to exit.'
            } elseif ($flagMutator.ExitCode -ne 0) {
                Add-Failure 'Expected the atomic flags-only mutation to succeed.'
            }
        }
        finally {
            $scanner = $originalScanner
            $flagMutator.Dispose()
        }
        [IO.File]::Copy(
            $normalFlagIndexTemplate,
            $flagDriftIndexPath,
            $true
        )
        if ($flagDriftResult.ExitCode -ne 2 -or
            $flagDriftResult.Output -notmatch
            'integrity: git-index-drift') {
            Add-Failure 'Expected a flags-only index mutation to be caught by the final raw debug comparison.'
        }

        # A tracked local marker configuration is itself a publication leak
        # boundary, regardless of whether its contents look sensitive.
        $trackedLocalRoot = New-CheckedGitFixture -Name 'tracked-local-marker'
        Set-Content `
            -LiteralPath (Join-Path $trackedLocalRoot '.private-markers.local') `
            -Value 'synthetic-local-only-rule' `
            -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $trackedLocalRoot `
            -Arguments @('add', '-f', '--', '.private-markers.local') `
            -Context 'Force-add local marker configuration')
        $trackedLocalResult = Invoke-Scanner -ScanPath $trackedLocalRoot
        if ($trackedLocalResult.ExitCode -ne 2 -or
            $trackedLocalResult.Output -notmatch
            'integrity: git-index-local-marker') {
            Add-Failure "Expected tracked local marker configuration to fail closed. Output: $($trackedLocalResult.Output.Trim())"
        }

        # Intent-to-add entries carry CE_INTENT_TO_ADD even though current Git
        # exposes the normal empty-blob OID. Reject both the present `.A` and
        # missing `.D` worktree states from the direct extended flag.
        $intentRoot = New-CheckedGitFixture -Name 'intent-to-add'
        Set-Content `
            -LiteralPath (Join-Path $intentRoot 'intent.md') `
            -Value 'synthetic clean content' `
            -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $intentRoot `
            -Arguments @('add', '--intent-to-add', '--', 'intent.md') `
            -Context 'Create intent-to-add entry')
        $intentResult = Invoke-Scanner -ScanPath $intentRoot
        if ($intentResult.ExitCode -ne 2 -or
            $intentResult.Output -notmatch
            'integrity: git-index-intent-to-add') {
            Add-Failure "Expected intent-to-add index entry to fail closed. Output: $($intentResult.Output.Trim())"
        }
        [IO.File]::Delete((Join-Path $intentRoot 'intent.md'))
        $missingIntentResult = Invoke-Scanner -ScanPath $intentRoot
        if ($missingIntentResult.ExitCode -ne 2 -or
            $missingIntentResult.Output -notmatch
            'integrity: git-index-intent-to-add') {
            Add-Failure "Expected missing intent-to-add index entry to fail closed. Output: $($missingIntentResult.Output.Trim())"
        }

        # A genuinely staged empty file has the same blob OID but not the
        # extended flag, so the direct flag parser must not reject it.
        $stagedEmptyRoot = New-CheckedGitFixture -Name 'staged-empty'
        [IO.File]::WriteAllBytes(
            (Join-Path $stagedEmptyRoot 'empty.md'),
            [byte[]]@()
        )
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $stagedEmptyRoot `
            -Arguments @('add', '--', 'empty.md') `
            -Context 'Stage genuine empty file')
        $stagedEmptyResult = Invoke-Scanner -ScanPath $stagedEmptyRoot
        if ($stagedEmptyResult.ExitCode -ne 0) {
            Add-Failure "Expected a genuine staged empty file to pass. Output: $($stagedEmptyResult.Output.Trim())"
        }

        # A gitlink has no text blob at the path and may hide a nested external
        # repository. A synthetic info-only cache entry avoids any network.
        $gitlinkRoot = New-CheckedGitFixture -Name 'gitlink'
        $syntheticCommitOid = ('1' * 40)
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $gitlinkRoot `
            -Arguments @(
                'update-index',
                '--add',
                '--info-only',
                '--cacheinfo',
                "160000,$syntheticCommitOid,vendor"
            ) `
            -Context 'Create synthetic gitlink entry')
        $gitlinkResult = Invoke-Scanner -ScanPath $gitlinkRoot
        if ($gitlinkResult.ExitCode -ne 2 -or
            $gitlinkResult.Output -notmatch 'integrity: git-index-gitlink') {
            Add-Failure "Expected gitlink index entry to fail closed. Output: $($gitlinkResult.Output.Trim())"
        }

        # Index-mode symlinks are scanned from their raw immutable blob only
        # when no worktree link exists, so no external target is followed.
        $indexLinkRoot = New-CheckedGitFixture -Name 'index-symlink'
        $linkSource = Join-Path $indexLinkRoot 'link-source.bin'
        $linkMarker = ('g' + 'hp_') + 'synthetic_link_target'
        [IO.File]::WriteAllText(
            $linkSource,
            "synthetic/$linkMarker",
            [Text.UTF8Encoding]::new($false)
        )
        $linkHashResult = Invoke-CheckedFixtureGit `
            -FixtureRoot $indexLinkRoot `
            -Arguments @('hash-object', '-w', '--', 'link-source.bin') `
            -Context 'Create synthetic symlink blob'
        $linkOid = [Text.Encoding]::ASCII.GetString(
            $linkHashResult.StdoutBytes
        ).Trim()
        if ($linkOid -notmatch '^[0-9a-f]{40,64}$') {
            Add-Failure 'Expected a valid object ID for the synthetic symlink blob.'
        } else {
            [void](Invoke-CheckedFixtureGit `
                -FixtureRoot $indexLinkRoot `
                -Arguments @(
                    'update-index',
                    '--add',
                    '--cacheinfo',
                    "120000,$linkOid,synthetic-link.md"
                ) `
                -Context 'Create synthetic index symlink')
            $indexLinkResult = Invoke-Scanner -ScanPath $indexLinkRoot
            if ($indexLinkResult.ExitCode -eq 0 -or
                $indexLinkResult.Output -notmatch
                'synthetic-link\.md \[index symlink; worktree missing\]') {
                Add-Failure "Expected missing worktree symlink to scan its index blob. Output: $($indexLinkResult.Output.Trim())"
            }
        }

        # A reparse/symlink directory in a tracked worktree path must be
        # rejected before its external leaf is opened.
        $reparseRoot = New-CheckedGitFixture -Name 'reparse-ancestor'
        $trackedDirectory = Join-Path $reparseRoot 'linked'
        New-Item -ItemType Directory -Path $trackedDirectory | Out-Null
        Set-Content `
            -LiteralPath (Join-Path $trackedDirectory 'leak.md') `
            -Value 'synthetic clean content' `
            -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $reparseRoot `
            -Arguments @('add', '--', 'linked/leak.md') `
            -Context 'Stage reparse ancestor fixture')
        $externalDirectory = Join-Path $tempRoot 'reparse-external-target'
        Move-Item `
            -LiteralPath $trackedDirectory `
            -Destination $externalDirectory
        $externalMarker = ('g' + 'hp_') + 'synthetic_external_target'
        Set-Content `
            -LiteralPath (Join-Path $externalDirectory 'leak.md') `
            -Value "external marker: $externalMarker" `
            -Encoding UTF8
        try {
            if ($runtimeIsWindows) {
                New-Item `
                    -ItemType Junction `
                    -Path $trackedDirectory `
                    -Target $externalDirectory |
                    Out-Null
            } else {
                New-Item `
                    -ItemType SymbolicLink `
                    -Path $trackedDirectory `
                    -Target $externalDirectory |
                    Out-Null
            }
            $reparseResult = Invoke-Scanner -ScanPath $reparseRoot
            if ($reparseResult.ExitCode -ne 2 -or
                $reparseResult.Output -notmatch
                'integrity: worktree-reparse-path' -or
                $reparseResult.Output.Contains($externalMarker)) {
                Add-Failure "Expected a reparse ancestor to fail before external content is read. Output: $($reparseResult.Output.Trim())"
            }
        }
        catch {
            Add-Failure 'Expected the synthetic reparse ancestor fixture to be creatable.'
        }

        # Replace refs can redirect cat-file away from the exact staged object.
        # The hermetic child environment must force the original marker blob.
        $replaceRoot = New-CheckedGitFixture -Name 'replace-object'
        $replacePath = Join-Path $replaceRoot 'replace.md'
        $replaceMarker = ('g' + 'hp_') + 'synthetic_replace_original'
        Set-Content `
            -LiteralPath $replacePath `
            -Value "original marker: $replaceMarker" `
            -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $replaceRoot `
            -Arguments @('add', '--', 'replace.md') `
            -Context 'Stage replace-ref marker')
        $originalOidResult = Invoke-CheckedFixtureGit `
            -FixtureRoot $replaceRoot `
            -Arguments @('rev-parse', ':replace.md') `
            -Context 'Resolve original marker object'
        $originalOid = [Text.Encoding]::ASCII.GetString(
            $originalOidResult.StdoutBytes
        ).Trim()
        $cleanBlobPath = Join-Path $replaceRoot 'clean-source.bin'
        [IO.File]::WriteAllText(
            $cleanBlobPath,
            'synthetic clean replacement',
            [Text.UTF8Encoding]::new($false)
        )
        $cleanOidResult = Invoke-CheckedFixtureGit `
            -FixtureRoot $replaceRoot `
            -Arguments @('hash-object', '-w', '--', 'clean-source.bin') `
            -Context 'Create clean replacement object'
        $cleanOid = [Text.Encoding]::ASCII.GetString(
            $cleanOidResult.StdoutBytes
        ).Trim()
        if ($originalOid -notmatch '^[0-9a-f]{40,64}$' -or
            $cleanOid -notmatch '^[0-9a-f]{40,64}$') {
            Add-Failure 'Expected valid object IDs for the replace-ref regression.'
        } else {
            $replaceRefs = Join-Path $replaceRoot '.git/refs/replace'
            New-Item -ItemType Directory -Path $replaceRefs -Force | Out-Null
            [IO.File]::WriteAllText(
                (Join-Path $replaceRefs $originalOid),
                "$cleanOid`n",
                [Text.UTF8Encoding]::new($false)
            )
            Remove-Item -LiteralPath $replacePath -Force
            $replaceResult = Invoke-Scanner -ScanPath $replaceRoot
            if ($replaceResult.ExitCode -eq 0 -or
                $replaceResult.Output -notmatch
                'replace\.md \[index; worktree missing\]') {
                Add-Failure "Expected replace refs to be disabled while scanning the original index blob. Output: $($replaceResult.Output.Trim())"
            }
        }

        # A corrupted index must stop at the Git boundary. Do not treat a
        # failed ls-files parse as an empty repository.
        $corruptRoot = New-CheckedGitFixture -Name 'corrupt-index'
        Set-Content `
            -LiteralPath (Join-Path $corruptRoot 'tracked.md') `
            -Value 'synthetic clean content' `
            -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $corruptRoot `
            -Arguments @('add', '--', 'tracked.md') `
            -Context 'Stage corrupt-index fixture')
        [IO.File]::WriteAllBytes(
            (Join-Path $corruptRoot '.git/index'),
            [byte[]](1, 2, 3, 4, 5, 6, 7)
        )
        $corruptResult = Invoke-Scanner -ScanPath $corruptRoot
        if ($corruptResult.ExitCode -ne 2 -or
            $corruptResult.Output -notmatch 'integrity: git-index-list') {
            Add-Failure "Expected a corrupt index to fail closed. Output: $($corruptResult.Output.Trim())"
        }

        # Build an actual unmerged index so stages 1-3 cannot be mistaken for a
        # normal stage-0 path.
        $conflictRoot = New-CheckedGitFixture -Name 'unmerged-index'
        $conflictPath = Join-Path $conflictRoot 'conflict.md'
        $syntheticEmail = 'synthetic' + '@example.invalid'
        Set-Content -LiteralPath $conflictPath -Value 'base' -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $conflictRoot `
            -Arguments @('add', '--', 'conflict.md') `
            -Context 'Stage conflict base')
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $conflictRoot `
            -Arguments @(
                '-c',
                'user.name=Synthetic',
                '-c',
                "user.email=$syntheticEmail",
                'commit',
                '--quiet',
                '-m',
                'base'
            ) `
            -Context 'Commit conflict base')
        $branchResult = Invoke-CheckedFixtureGit `
            -FixtureRoot $conflictRoot `
            -Arguments @('branch', '--show-current') `
            -Context 'Resolve default branch'
        $defaultBranch = [Text.Encoding]::UTF8.GetString(
            $branchResult.StdoutBytes
        ).Trim()
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $conflictRoot `
            -Arguments @('checkout', '--quiet', '-b', 'synthetic-side') `
            -Context 'Create conflict side branch')
        Set-Content -LiteralPath $conflictPath -Value 'side' -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $conflictRoot `
            -Arguments @('add', '--', 'conflict.md') `
            -Context 'Stage conflict side')
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $conflictRoot `
            -Arguments @(
                '-c',
                'user.name=Synthetic',
                '-c',
                "user.email=$syntheticEmail",
                'commit',
                '--quiet',
                '-m',
                'side'
            ) `
            -Context 'Commit conflict side')
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $conflictRoot `
            -Arguments @('checkout', '--quiet', $defaultBranch) `
            -Context 'Return to default branch')
        Set-Content -LiteralPath $conflictPath -Value 'main' -Encoding UTF8
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $conflictRoot `
            -Arguments @('add', '--', 'conflict.md') `
            -Context 'Stage conflict main')
        [void](Invoke-CheckedFixtureGit `
            -FixtureRoot $conflictRoot `
            -Arguments @(
                '-c',
                'user.name=Synthetic',
                '-c',
                "user.email=$syntheticEmail",
                'commit',
                '--quiet',
                '-m',
                'main'
            ) `
            -Context 'Commit conflict main')
        $mergeResult = Invoke-IsolatedGit `
            -GitPath $gitCommand.Source `
            -WorkingDirectory $conflictRoot `
            -IsolationRoot $gitIsolationRoot `
            -Arguments @(
                '-c',
                'user.name=Synthetic',
                '-c',
                "user.email=$syntheticEmail",
                'merge',
                '--no-edit',
                'synthetic-side'
            ) `
            -InheritedEnvironment $adversarialEnvironment
        if ($mergeResult.ExitCode -eq 0 -or
            -not (Test-BoundedResultHealthy -Result $mergeResult)) {
            Add-Failure "Expected the synthetic branch merge to produce an unmerged index. Output: $($mergeResult.Output.Trim())"
        } else {
            $unmergedResult = Invoke-CheckedFixtureGit `
                -FixtureRoot $conflictRoot `
                -Arguments @('ls-files', '--unmerged', '--') `
                -Context 'Inspect synthetic unmerged index'
            if ($unmergedResult.StdoutBytes.Length -eq 0) {
                Add-Failure "Expected merge failure to leave unmerged index stages. Merge output: $($mergeResult.Output.Trim())"
            } else {
                $conflictResult = Invoke-Scanner -ScanPath $conflictRoot
                if ($conflictResult.ExitCode -ne 2 -or
                    $conflictResult.Output -notmatch
                    'integrity: git-index-conflict') {
                    Add-Failure "Expected an unmerged index to fail closed. Output: $($conflictResult.Output.Trim())"
                }
            }
        }

        if (-not $runtimeIsWindows) {
            # POSIX permits control characters in file names. Findings must
            # escape them so a path cannot forge extra CI log lines.
            $controlRoot = New-CheckedGitFixture -Name 'control-path'
            $controlName = 'control' + [char]10 + 'name.md'
            $controlMarker = ('g' + 'hp_') + 'synthetic_control_path'
            Set-Content `
                -LiteralPath (Join-Path $controlRoot $controlName) `
                -Value "synthetic marker: $controlMarker" `
                -Encoding UTF8
            [void](Invoke-CheckedFixtureGit `
                -FixtureRoot $controlRoot `
                -Arguments @('add', '-A') `
                -Context 'Stage control-character path')
            $controlResult = Invoke-Scanner -ScanPath $controlRoot
            if ($controlResult.ExitCode -eq 0 -or
                $controlResult.Output -notmatch
                'control\\u000aname\.md') {
                Add-Failure "Expected control characters in displayed paths to be escaped. Output: $($controlResult.Output.Trim())"
            }

            # Feed three malformed ls-files byte streams through the public
            # entrypoint. The fake executable is a local synthetic fixture;
            # no repository content or external command is consulted.
            $fakeGitDirectory = Join-Path $tempRoot 'fake-git-bin'
            $fakeGitRoot = Join-Path $tempRoot 'fake-git-root'
            New-Item -ItemType Directory -Path $fakeGitDirectory | Out-Null
            New-Item -ItemType Directory -Path $fakeGitRoot | Out-Null
            $fakeGitPath = Join-Path $fakeGitDirectory 'git'
            $fakeGitScript = @'
#!/bin/sh
case "$*" in
  *"rev-parse --is-inside-work-tree --show-prefix"*)
    case "${MARKDOWN_MERGE_FAKE_GIT_PROBE_MODE-}" in
      missing-final-lf)
        printf 'true\n'
        ;;
      extra-record)
        printf 'true\n\nextra\n'
        ;;
      nul)
        printf 'true\n\000\n'
        ;;
      invalid-utf8)
        printf '\377\n\n'
        ;;
      uppercase)
        printf 'TRUE\n\n'
        ;;
      whitespace)
        printf ' true\n\n'
        ;;
      *)
        printf '%s\n%s\n' \
          "${MARKDOWN_MERGE_FAKE_GIT_INSIDE-true}" \
          "${MARKDOWN_MERGE_FAKE_GIT_PREFIX-}"
        ;;
    esac
    ;;
  *"ls-files -z --stage --debug"*)
    case "$MARKDOWN_MERGE_FAKE_GIT_MODE" in
      header)
        printf 'bad-header\tfile.md\0  ctime: 0:0\n  mtime: 0:0\n  dev: 0\tino: 0\n  uid: 0\tgid: 0\n  size: 0\tflags: 0\n'
        ;;
      path)
        printf '100644 1111111111111111111111111111111111111111 0\t../escape.md\0  ctime: 0:0\n  mtime: 0:0\n  dev: 0\tino: 0\n  uid: 0\tgid: 0\n  size: 0\tflags: 0\n'
        ;;
    esac
    ;;
  *"ls-files -z --stage"*)
    case "$MARKDOWN_MERGE_FAKE_GIT_MODE" in
      nul)
        printf '100644 1111111111111111111111111111111111111111 0\tfile.md'
        ;;
      header)
        printf 'bad-header\tfile.md\0'
        ;;
      path)
        printf '100644 1111111111111111111111111111111111111111 0\t../escape.md\0'
        ;;
    esac
    ;;
  *)
    exit 1
    ;;
esac
'@
            [IO.File]::WriteAllText(
                $fakeGitPath,
                $fakeGitScript,
                [Text.UTF8Encoding]::new($false)
            )
            $fakeChmodResult = Invoke-PrivateMarkerBoundedProcess `
                -FileName $chmodCommand.Source `
                -Arguments @('+x', $fakeGitPath) `
                -IsolationRoot (Join-Path $tempRoot 'fake-git-chmod') `
                -TimeoutMilliseconds 10000
            if ($fakeChmodResult.ExitCode -ne 0 -or
                -not (Test-BoundedResultHealthy -Result $fakeChmodResult)) {
                Add-Failure 'Expected the synthetic fake Git fixture to be executable.'
            } else {
                $fakePath = $fakeGitDirectory +
                    [IO.Path]::PathSeparator +
                    $env:PATH
                foreach ($malformedCase in @(
                    @{
                        Mode = 'nul'
                        Reason = 'git-index-nul'
                    },
                    @{
                        Mode = 'header'
                        Reason = 'git-index-header'
                    },
                    @{
                        Mode = 'path'
                        Reason = 'git-index-path'
                    }
                )) {
                    $malformedResult = Invoke-Scanner `
                        -ScanPath $fakeGitRoot `
                        -InheritedEnvironment @{
                            PATH = $fakePath
                            MARKDOWN_MERGE_FAKE_GIT_MODE =
                                $malformedCase.Mode
                        }
                    if ($malformedResult.ExitCode -ne 2 -or
                        $malformedResult.Output -notmatch
                        ("integrity: " +
                            [regex]::Escape($malformedCase.Reason))) {
                        Add-Failure "Expected malformed $($malformedCase.Mode) index output to fail closed. Output: $($malformedResult.Output.Trim())"
                    }
                }

                # A non-empty Git prefix means the supplied scan path is below
                # the repository root. Keep this fail-closed guard while the
                # implementation tolerates equivalent macOS ancestor aliases.
                $fakePrefixResult = Invoke-Scanner `
                    -ScanPath $fakeGitRoot `
                    -InheritedEnvironment @{
                        PATH = $fakePath
                        MARKDOWN_MERGE_FAKE_GIT_MODE = 'nul'
                        MARKDOWN_MERGE_FAKE_GIT_PREFIX = 'sub/'
                    }
                if ($fakePrefixResult.ExitCode -ne 2 -or
                    $fakePrefixResult.Output -notmatch
                    'integrity: git-root-mismatch') {
                    Add-Failure "Expected a non-empty Git prefix to fail closed. Output: $($fakePrefixResult.Output.Trim())"
                }

                # A Git directory or bare repository can return an empty
                # prefix, but it is not a worktree root and must be rejected.
                $fakeNonWorktreeResult = Invoke-Scanner `
                    -ScanPath $fakeGitRoot `
                    -InheritedEnvironment @{
                        PATH = $fakePath
                        MARKDOWN_MERGE_FAKE_GIT_MODE = 'nul'
                        MARKDOWN_MERGE_FAKE_GIT_INSIDE = 'false'
                    }
                if ($fakeNonWorktreeResult.ExitCode -ne 2 -or
                    $fakeNonWorktreeResult.Output -notmatch
                    'integrity: git-root-mismatch') {
                    Add-Failure "Expected a non-worktree Git root to fail closed. Output: $($fakeNonWorktreeResult.Output.Trim())"
                }

                foreach ($malformedProbeCase in @(
                    @{
                        Mode = 'missing-final-lf'
                        Reason = 'git-top-level-record'
                    },
                    @{
                        Mode = 'extra-record'
                        Reason = 'git-top-level-record'
                    },
                    @{
                        Mode = 'nul'
                        Reason = 'git-top-level-record'
                    },
                    @{
                        Mode = 'invalid-utf8'
                        Reason = 'git-top-level-encoding'
                    },
                    @{
                        Mode = 'uppercase'
                        Reason = 'git-root-mismatch'
                    },
                    @{
                        Mode = 'whitespace'
                        Reason = 'git-root-mismatch'
                    }
                )) {
                    $malformedProbeResult = Invoke-Scanner `
                        -ScanPath $fakeGitRoot `
                        -InheritedEnvironment @{
                            PATH = $fakePath
                            MARKDOWN_MERGE_FAKE_GIT_MODE = 'nul'
                            MARKDOWN_MERGE_FAKE_GIT_PROBE_MODE =
                                $malformedProbeCase.Mode
                        }
                    if ($malformedProbeResult.ExitCode -ne 2 -or
                        $malformedProbeResult.Output -notmatch
                        ("integrity: " +
                            [regex]::Escape(
                                $malformedProbeCase.Reason
                            ))) {
                        Add-Failure "Expected malformed $($malformedProbeCase.Mode) Git root probe output to fail closed. Output: $($malformedProbeResult.Output.Trim())"
                    }
                }
            }
        }
    }
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}

if ($failures.Count -gt 0) {
    Write-Host 'Private marker scan self-test failed:'
    foreach ($failure in $failures) {
        Write-Host "- $failure"
    }
    exit 1
}

Write-Host 'Private marker scan self-test passed.'
exit 0
