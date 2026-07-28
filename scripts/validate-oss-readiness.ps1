[CmdletBinding()]
param(
    [string]$Path = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($scriptRoot)) {
    $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

if ([string]::IsNullOrWhiteSpace($Path)) {
    $Path = Split-Path -Parent $scriptRoot
}

$root = (Resolve-Path -LiteralPath $Path).Path
$failures = New-Object System.Collections.Generic.List[string]

function Add-Failure {
    param([string]$Message)
    $failures.Add($Message) | Out-Null
}

function Get-RepoFilePath {
    param([string]$RelativePath)
    return Join-Path $root $RelativePath
}

function Assert-FileExists {
    param([string]$RelativePath)

    $filePath = Get-RepoFilePath -RelativePath $RelativePath
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
        Add-Failure "Missing required file: $RelativePath"
    }
}

function Assert-FileContains {
    param(
        [string]$RelativePath,
        [string]$Pattern,
        [string]$Description
    )

    $filePath = Get-RepoFilePath -RelativePath $RelativePath
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
        Add-Failure "Cannot inspect missing file: $RelativePath ($Description)"
        return
    }

    # Windows PowerShell 5.1 の既定 ANSI 解釈に依存すると、BOM なし UTF-8 の
    # 日本語契約が文字化けして false negative になる。明示 UTF-8 で読む。
    $content = Get-Content -LiteralPath $filePath -Raw -Encoding UTF8
    if ($content -notmatch $Pattern) {
        Add-Failure "$RelativePath is missing: $Description"
    }
}

function Get-ExactLineIndexes {
    param(
        [string[]]$Lines,
        [string]$Expected
    )

    $indexes = New-Object System.Collections.Generic.List[int]
    for ($index = 0; $index -lt $Lines.Count; $index++) {
        if ([string]::Equals($Lines[$index], $Expected, [System.StringComparison]::Ordinal)) {
            $indexes.Add($index) | Out-Null
        }
    }
    return $indexes.ToArray()
}

function Get-MatchingLineCount {
    param(
        [string[]]$Lines,
        [string]$Pattern
    )

    $count = 0
    foreach ($line in $Lines) {
        if ([regex]::IsMatch($line, $Pattern)) {
            $count++
        }
    }
    return $count
}

function Get-LeadingSpaceCount {
    param([string]$Line)

    return [regex]::Match($Line, '^ *').Length
}

function Test-IgnorableYamlLine {
    param([string]$Line)

    return [string]::IsNullOrWhiteSpace($Line) -or $Line -match '^ *#'
}

function Test-WorkflowExecutionContract {
    param([string]$Content)

    # YAML全般を再実装せず、このrepositoryが固定するvalidate jobのindent構造だけを
    # 逐行で検査する。実効keyを引用符・colon前空白・explicit key等で上書きされると
    # text契約がfalse-greenになるため、contract範囲はcanonical simple keyだけを許可する。
    $lines = @($Content -split '\r?\n')
    $jobsIndexes = @(Get-ExactLineIndexes -Lines $lines -Expected 'jobs:')
    if (
        $jobsIndexes.Count -ne 1 -or
        (Get-MatchingLineCount -Lines $lines -Pattern '^jobs:(?:[ \t].*)?$') -ne 1
    ) {
        return $false
    }

    # root mappingはunquoted key + colon直結に固定する。これにより `jobs :`、
    # `"jobs":`、explicit key、tagged keyなど意味的に同じ別表記の重複を拒否する。
    $expectedRootLines = @(
        'name: Validate',
        'on:',
        'permissions:',
        'jobs:'
    )
    $actualRootLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $lines) {
        if (Test-IgnorableYamlLine -Line $line) {
            continue
        }
        if ((Get-LeadingSpaceCount -Line $line) -eq 0) {
            if ($line -notmatch '^[A-Za-z0-9_-]+:(?:[ \t].*)?$') {
                return $false
            }
            $actualRootLines.Add($line) | Out-Null
        }
    }
    if ($actualRootLines.Count -ne $expectedRootLines.Count) {
        return $false
    }
    for ($index = 0; $index -lt $expectedRootLines.Count; $index++) {
        if ($actualRootLines[$index] -cne $expectedRootLines[$index]) {
            return $false
        }
    }

    # jobsより前のtrigger / permissionも既知のclosed semantic line列へ固定する。
    # 先頭name等でmultiline flow scalarを開き、以降の正しいworkflow text全体を
    # scalar本文へ退避するvalid YAMLを、root key textだけで誤受理しない。
    $expectedPreludeLines = @(
        'name: Validate',
        'on:',
        '  pull_request:',
        '  push:',
        '    branches:',
        '      - main',
        'permissions:',
        '  contents: read',
        'jobs:'
    )
    $actualPreludeLines = New-Object System.Collections.Generic.List[string]
    for ($index = 0; $index -le $jobsIndexes[0]; $index++) {
        if (-not (Test-IgnorableYamlLine -Line $lines[$index])) {
            $actualPreludeLines.Add($lines[$index]) | Out-Null
        }
    }
    if ($actualPreludeLines.Count -ne $expectedPreludeLines.Count) {
        return $false
    }
    for ($index = 0; $index -lt $expectedPreludeLines.Count; $index++) {
        if ($actualPreludeLines[$index] -cne $expectedPreludeLines[$index]) {
            return $false
        }
    }

    # jobs mappingは次のcolumn-0 contentで終了する。root block scalarや別mappingに
    # 置いた2-space validate decoyを実行jobとして誤認しない。
    $jobsEnd = $lines.Count
    for ($index = $jobsIndexes[0] + 1; $index -lt $lines.Count; $index++) {
        if (
            -not (Test-IgnorableYamlLine -Line $lines[$index]) -and
            (Get-LeadingSpaceCount -Line $lines[$index]) -eq 0
        ) {
            $jobsEnd = $index
            break
        }
    }

    # jobs直下はcanonicalなblock job keyだけを許可する。quoted/spaced/tagged keyや
    # inline mappingを許すと、正しいvalidate textを別jobやscalarへ退避できてしまう。
    $validateIndexes = New-Object System.Collections.Generic.List[int]
    $directJobHeaders = New-Object System.Collections.Generic.List[string]
    for ($index = $jobsIndexes[0] + 1; $index -lt $jobsEnd; $index++) {
        $line = $lines[$index]
        if (Test-IgnorableYamlLine -Line $line) {
            continue
        }
        $indent = Get-LeadingSpaceCount -Line $line
        if ($indent -lt 4) {
            if ($indent -ne 2) {
                return $false
            }
            if ($line -notmatch '^  [A-Za-z0-9_-]+:[ \t]*$') {
                return $false
            }
            $directJobHeaders.Add($line) | Out-Null
            if ([string]::Equals($line, '  validate:', [System.StringComparison]::Ordinal)) {
                $validateIndexes.Add($index) | Out-Null
            }
        }
    }
    if (
        $validateIndexes.Count -ne 1 -or
        $directJobHeaders.Count -ne 1 -or
        $directJobHeaders[0] -cne '  validate:'
    ) {
        return $false
    }
    # YAML flow scalarはdedentを跨げるため、先行job内でquoteを開くと後続の正しい
    # validate text全体をscalar化できる。validateをjobs直下の最初のsemantic
    # contentへ固定し、安全な追加jobはvalidateの後だけに置く。
    for ($index = $jobsIndexes[0] + 1; $index -lt $validateIndexes[0]; $index++) {
        if (-not (Test-IgnorableYamlLine -Line $lines[$index])) {
            return $false
        }
    }

    # 次の2-space job keyまでをvalidate job本体とし、他jobの正しいdecoyを拒否する。
    $validateEnd = $jobsEnd
    for ($index = $validateIndexes[0] + 1; $index -lt $jobsEnd; $index++) {
        if (
            -not (Test-IgnorableYamlLine -Line $lines[$index]) -and
            (Get-LeadingSpaceCount -Line $lines[$index]) -eq 2
        ) {
            $validateEnd = $index
            break
        }
    }
    $validateLines = @($lines[$validateIndexes[0]..($validateEnd - 1)])

    # validate jobはcomment/blankを除いたsemantic line列全体を固定する。各stepの
    # name + uses/shell + run/bodyと順序を一つのcontractとして比較するため、step
    # 削除・並べ替え・root scalar decoy・multiline scalar退避を同時に拒否できる。
    $expectedValidateLines = @(
        '  validate:',
        '    name: Validate skill repository (${{ matrix.os }})',
        '    strategy:',
        '      fail-fast: false',
        '      matrix:',
        '        os: [windows-latest, ubuntu-latest, macos-15]',
        '    runs-on: ${{ matrix.os }}',
        '    timeout-minutes: 25',
        '    steps:',
        '      - name: Check out repository',
        '        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5',
        '      - name: Set up Python',
        '        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0',
        '        with:',
        "          python-version: '3.x'",
        '      - name: Validate OSS readiness',
        '        shell: pwsh',
        '        run: ./scripts/validate-oss-readiness.ps1',
        '      - name: Test reference implementation (fixtures, idempotency, trap proof)',
        '        shell: pwsh',
        '        run: python scripts/test_merge_section.py',
        '      - name: Test private marker scan',
        '        shell: pwsh',
        '        run: ./scripts/test-scan-private-markers.ps1',
        '      - name: Scan for private markers',
        '        shell: pwsh',
        '        run: ./scripts/scan-private-markers.ps1',
        '      - name: Validate scanner with Windows PowerShell 5.1',
        "        if: runner.os == 'Windows'",
        '        shell: powershell',
        '        run: |',
        '          ./scripts/validate-oss-readiness.ps1',
        '          ./scripts/test-scan-private-markers.ps1',
        '          ./scripts/scan-private-markers.ps1',
        '      - name: Check whitespace',
        '        shell: pwsh',
        '        run: git diff-tree --check 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD'
    )
    $actualValidateLines = New-Object System.Collections.Generic.List[string]
    foreach ($line in $validateLines) {
        if (-not (Test-IgnorableYamlLine -Line $line)) {
            $actualValidateLines.Add($line) | Out-Null
        }
    }
    if ($actualValidateLines.Count -ne $expectedValidateLines.Count) {
        return $false
    }
    for ($index = 0; $index -lt $expectedValidateLines.Count; $index++) {
        if ($actualValidateLines[$index] -cne $expectedValidateLines[$index]) {
            return $false
        }
    }
    # 以降の補助的なcount / adjacency検査も同じsemantic line列を使う。
    # raw line indexへ戻すと、意味を変えないcomment / blankの位置でfalse-negativeになる。
    $validateLines = @($actualValidateLines.ToArray())

    $jobNameIndexes = @(
        Get-ExactLineIndexes -Lines $validateLines -Expected '    name: Validate skill repository (${{ matrix.os }})'
    )
    $strategyIndexes = @(Get-ExactLineIndexes -Lines $validateLines -Expected '    strategy:')
    $runsOnIndexes = @(Get-ExactLineIndexes -Lines $validateLines -Expected '    runs-on: ${{ matrix.os }}')
    $timeoutIndexes = @(Get-ExactLineIndexes -Lines $validateLines -Expected '    timeout-minutes: 25')
    $stepsIndexes = @(Get-ExactLineIndexes -Lines $validateLines -Expected '    steps:')
    $matrixOsIndexes = @(Get-ExactLineIndexes -Lines $validateLines -Expected '        os: [windows-latest, ubuntu-latest, macos-15]')
    if (
        $jobNameIndexes.Count -ne 1 -or
        $strategyIndexes.Count -ne 1 -or
        $runsOnIndexes.Count -ne 1 -or
        $timeoutIndexes.Count -ne 1 -or
        $stepsIndexes.Count -ne 1 -or
        $matrixOsIndexes.Count -ne 1
    ) {
        return $false
    }
    if (
        (Get-MatchingLineCount -Lines $validateLines -Pattern '^    strategy:') -ne 1 -or
        (Get-MatchingLineCount -Lines $validateLines -Pattern '^    runs-on:') -ne 1 -or
        (Get-MatchingLineCount -Lines $validateLines -Pattern '^    timeout-minutes:') -ne 1 -or
        (Get-MatchingLineCount -Lines $validateLines -Pattern '^      fail-fast:') -ne 1 -or
        (Get-MatchingLineCount -Lines $validateLines -Pattern '^      matrix:') -ne 1 -or
        (Get-MatchingLineCount -Lines $validateLines -Pattern '^        os:') -ne 1
    ) {
        return $false
    }

    $strategyIndex = $strategyIndexes[0]
    if (
        $strategyIndex + 3 -ge $validateLines.Count -or
        $validateLines[$strategyIndex + 1] -cne '      fail-fast: false' -or
        $validateLines[$strategyIndex + 2] -cne '      matrix:' -or
        $validateLines[$strategyIndex + 3] -cne '        os: [windows-latest, ubuntu-latest, macos-15]' -or
        $runsOnIndexes[0] -ne ($strategyIndex + 4) -or
        $timeoutIndexes[0] -ne ($strategyIndex + 5)
    ) {
        return $false
    }

    # PS5.1 stepはname / condition / shell / runを同じstep内の連続行で固定する。
    $ps51NameIndexes = @(
        Get-ExactLineIndexes -Lines $validateLines -Expected '      - name: Validate scanner with Windows PowerShell 5.1'
    )
    $ps51ShellIndexes = @(Get-ExactLineIndexes -Lines $validateLines -Expected '        shell: powershell')
    if ($ps51NameIndexes.Count -ne 1 -or $ps51ShellIndexes.Count -ne 1) {
        return $false
    }
    $ps51Index = $ps51NameIndexes[0]
    if (
        $ps51Index + 3 -ge $validateLines.Count -or
        $validateLines[$ps51Index + 1] -cne "        if: runner.os == 'Windows'" -or
        $validateLines[$ps51Index + 2] -cne '        shell: powershell' -or
        $validateLines[$ps51Index + 3] -cne '        run: |'
    ) {
        return $false
    }
    $ps51End = $validateLines.Count
    for ($index = $ps51Index + 1; $index -lt $validateLines.Count; $index++) {
        if ($validateLines[$index] -match '^      - ') {
            $ps51End = $index
            break
        }
    }
    $ps51Lines = @($validateLines[$ps51Index..($ps51End - 1)])
    if (
        (Get-MatchingLineCount -Lines $ps51Lines -Pattern '^        if:') -ne 1 -or
        (Get-MatchingLineCount -Lines $ps51Lines -Pattern '^        shell:') -ne 1 -or
        (Get-MatchingLineCount -Lines $ps51Lines -Pattern '^        run:') -ne 1
    ) {
        return $false
    }

    return $true
}

function Assert-WorkflowExecutionContract {
    $relativePath = '.github/workflows/validate.yml'
    $filePath = Get-RepoFilePath -RelativePath $relativePath
    if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
        Add-Failure "Cannot inspect missing file: $relativePath (structured runner contract)"
        return
    }

    $content = Get-Content -LiteralPath $filePath -Raw -Encoding UTF8
    if (-not (Test-WorkflowExecutionContract -Content $content)) {
        Add-Failure "$relativePath does not connect the exact OS matrix, matrix runner, timeout, and Windows-only PS5.1 step."
        return
    }

    # 既知false-greenをin-memory mutationし、validator自身が必ず拒否する。
    $requiredStepBlocks = [ordered]@{
        checkout = @(
            '      - name: Check out repository',
            '        uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5'
        ) -join "`n"
        setup_python = @(
            '      - name: Set up Python',
            '        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0',
            '        with:',
            "          python-version: '3.x'"
        ) -join "`n"
        readiness = @(
            '      - name: Validate OSS readiness',
            '        shell: pwsh',
            '        run: ./scripts/validate-oss-readiness.ps1'
        ) -join "`n"
        reference_tests = @(
            '      - name: Test reference implementation (fixtures, idempotency, trap proof)',
            '        shell: pwsh',
            '        run: python scripts/test_merge_section.py'
        ) -join "`n"
        scanner_self_test = @(
            '      - name: Test private marker scan',
            '        shell: pwsh',
            '        run: ./scripts/test-scan-private-markers.ps1'
        ) -join "`n"
        scanner = @(
            '      - name: Scan for private markers',
            '        shell: pwsh',
            '        run: ./scripts/scan-private-markers.ps1'
        ) -join "`n"
        ps51 = @(
            '      - name: Validate scanner with Windows PowerShell 5.1',
            "        if: runner.os == 'Windows'",
            '        shell: powershell',
            '        run: |',
            '          ./scripts/validate-oss-readiness.ps1',
            '          ./scripts/test-scan-private-markers.ps1',
            '          ./scripts/scan-private-markers.ps1'
        ) -join "`n"
        whitespace = @(
            '      - name: Check whitespace',
            '        shell: pwsh',
            '        # A fresh checkout has no worktree/index diff, so `git diff --check`',
            '        # would be vacuous here. Diff the committed tree against the empty',
            '        # tree (the SHA-1 empty-tree constant) so whitespace errors in',
            '        # committed content actually fail the job (exit 2 on findings).',
            '        run: git diff-tree --check 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD'
        ) -join "`n"
    }
    foreach ($requiredStep in $requiredStepBlocks.GetEnumerator()) {
        $missingStepMutation = $content.Replace($requiredStep.Value, '')
        if (
            $missingStepMutation -ceq $content -or
            (Test-WorkflowExecutionContract -Content $missingStepMutation)
        ) {
            Add-Failure "Workflow contract self-test did not reject a missing $($requiredStep.Key) step."
        }
    }

    # supply-chain pinは値だけでなくimmutable形も自己検証する。mutable majorや
    # warning原因だった旧revisionへ戻しても、正しいstep名/入力のdecoyで合格させない。
    $canonicalSetupPythonUse =
        '        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0'
    $mutableSetupPythonMutation = $content.Replace(
        $canonicalSetupPythonUse,
        '        uses: actions/setup-python@v7'
    )
    if (
        $mutableSetupPythonMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $mutableSetupPythonMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a mutable setup-python major tag.'
    }

    $oldSetupPythonMutation = $content.Replace(
        $canonicalSetupPythonUse,
        '        uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5'
    )
    if (
        $oldSetupPythonMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $oldSetupPythonMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject the deprecated setup-python revision.'
    }

    $adjacentTestSteps = $requiredStepBlocks.readiness + "`n`n" + $requiredStepBlocks.reference_tests
    $reorderedTestSteps = $requiredStepBlocks.reference_tests + "`n`n" + $requiredStepBlocks.readiness
    $reorderedStepsMutation = $content.Replace($adjacentTestSteps, $reorderedTestSteps)
    if (
        $reorderedStepsMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $reorderedStepsMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject reordered validation steps.'
    }

    $referenceWithoutStep = $content.Replace($requiredStepBlocks.reference_tests, '')
    $rootReferenceDecoyMutation = $referenceWithoutStep + "`nrun-name: |`n  test_merge_section.py`n"
    if (
        $referenceWithoutStep -ceq $content -or
        (Test-WorkflowExecutionContract -Content $rootReferenceDecoyMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a missing reference step plus root scalar decoy.'
    }

    $runsOnMutation = $content.Replace(
        '    runs-on: ${{ matrix.os }}',
        '    runs-on: ubuntu-latest'
    )
    if (
        $runsOnMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $runsOnMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a matrix disconnected from runs-on.'
    }

    $matrixWithoutMac = $content.Replace(
        '        os: [windows-latest, ubuntu-latest, macos-15]',
        '        os: [windows-latest, ubuntu-latest]'
    )
    $matrixDecoyMutation = $matrixWithoutMac.Replace(
        '        run: ./scripts/validate-oss-readiness.ps1',
        "        run: |`n          os: [windows-latest, ubuntu-latest, macos-15]`n          ./scripts/validate-oss-readiness.ps1"
    )
    if (
        $matrixWithoutMac -ceq $content -or
        $matrixDecoyMutation -ceq $matrixWithoutMac -or
        (Test-WorkflowExecutionContract -Content $matrixDecoyMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a block-scalar matrix decoy.'
    }

    $ps51WrongCondition = $content.Replace(
        "        if: runner.os == 'Windows'",
        "        if: runner.os != 'Windows'"
    )
    $ps51DecoyMutation = $ps51WrongCondition.Replace(
        '          ./scripts/validate-oss-readiness.ps1',
        "          if: runner.os == 'Windows'`n          ./scripts/validate-oss-readiness.ps1"
    )
    if (
        $ps51WrongCondition -ceq $content -or
        $ps51DecoyMutation -ceq $ps51WrongCondition -or
        (Test-WorkflowExecutionContract -Content $ps51DecoyMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a block-scalar PS5.1 condition decoy.'
    }

    $detachedJobMutation = $content.Replace(
        "jobs:`n  validate:",
        "jobs:`n  actual:`n    runs-on: ubuntu-latest`n    steps: []`nrun-name: |`n  validate:"
    )
    if (
        $detachedJobMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $detachedJobMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject validate content outside jobs.'
    }

    $oneSpaceParentMutation = $content.Replace(
        "jobs:`n  validate:",
        "jobs:`n actual:`n  runs-on: ubuntu-latest`n  validate:"
    )
    if (
        $oneSpaceParentMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $oneSpaceParentMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject validate content nested below a one-space job.'
    }

    $noncanonicalWrapperMutation = $content.Replace(
        "  validate:`n    name:",
        "  validate:`n   wrapper:`n    name:"
    )
    if (
        $noncanonicalWrapperMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $noncanonicalWrapperMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject contract content below a noncanonical wrapper.'
    }

    $quotedScalarDecoyMutation = $content.Replace(
        '    name: Validate skill repository (${{ matrix.os }})',
        '    name: "Validate skill repository (${{ matrix.os }})'
    ).Replace(
        '        run: git diff-tree --check 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD',
        '        run: git diff-tree --check 4b825dc642cb6eb9a060e54bf8d69288fbee4904 HEAD"'
    )
    if (
        $quotedScalarDecoyMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $quotedScalarDecoyMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject contract text inside a multiline quoted scalar.'
    }

    $rootQuotedScalarMutation = [regex]::Replace(
        $content,
        '\Aname: Validate(?=\r?\n)',
        'name: "Validate'
    ) + "`nrun-name: closed`"`n"
    if (
        $rootQuotedScalarMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $rootQuotedScalarMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject the whole workflow inside a root multiline scalar.'
    }

    $precedingJobScalarMutation = $content.Replace(
        "jobs:`n  validate:",
        "jobs:`n  other:`n    name: `"start`n  validate:"
    ) + "  # close`"`n"
    if (
        $precedingJobScalarMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $precedingJobScalarMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject validate text inside a preceding job scalar.'
    }

    $quotedDetachedJobMutation = $content.Replace(
        "jobs:`n  validate:",
        "jobs:`n  actual:`n    runs-on: ubuntu-latest`n    steps: []`n`"run-name`": |`n  validate:"
    )
    if (
        $quotedDetachedJobMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $quotedDetachedJobMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject validate content below a quoted root key.'
    }

    $spacedDetachedJobMutation = $content.Replace(
        "jobs:`n  validate:",
        "jobs:`n  actual:`n    runs-on: ubuntu-latest`n    steps: []`nrun-name : |`n  validate:"
    )
    if (
        $spacedDetachedJobMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $spacedDetachedJobMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject validate content below a spaced root key.'
    }

    $duplicateRunsOnMutation = $content.Replace(
        '    timeout-minutes: 25',
        "    timeout-minutes: 25`n    runs-on: ubuntu-latest"
    )
    if (
        $duplicateRunsOnMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $duplicateRunsOnMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a duplicate runs-on key.'
    }

    $quotedDuplicateRunsOnMutation = $content.Replace(
        '    timeout-minutes: 25',
        "    timeout-minutes: 25`n    `"runs-on`": ubuntu-latest"
    )
    if (
        $quotedDuplicateRunsOnMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $quotedDuplicateRunsOnMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a quoted duplicate runs-on key.'
    }

    $spacedDuplicateRunsOnMutation = $content.Replace(
        '    timeout-minutes: 25',
        "    timeout-minutes: 25`n    runs-on : ubuntu-latest"
    )
    if (
        $spacedDuplicateRunsOnMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $spacedDuplicateRunsOnMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a spaced duplicate runs-on key.'
    }

    $duplicateTimeoutMutation = $content.Replace(
        '    timeout-minutes: 25',
        "    timeout-minutes: 25`n    timeout-minutes: 360"
    )
    if (
        $duplicateTimeoutMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $duplicateTimeoutMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a duplicate timeout key.'
    }

    $quotedDuplicateTimeoutMutation = $content.Replace(
        '    timeout-minutes: 25',
        "    timeout-minutes: 25`n    'timeout-minutes': 360"
    )
    if (
        $quotedDuplicateTimeoutMutation -ceq $content -or
        (Test-WorkflowExecutionContract -Content $quotedDuplicateTimeoutMutation)
    ) {
        Add-Failure 'Workflow contract self-test did not reject a quoted duplicate timeout key.'
    }

    $spacedDuplicateJobsMutation = $content + "`njobs :`n  actual:`n    runs-on: ubuntu-latest`n    steps: []`n"
    if (Test-WorkflowExecutionContract -Content $spacedDuplicateJobsMutation) {
        Add-Failure 'Workflow contract self-test did not reject a spaced duplicate jobs key.'
    }

    $quotedDuplicateJobsMutation = $content + "`n`"jobs`":`n  actual:`n    runs-on: ubuntu-latest`n    steps: []`n"
    if (Test-WorkflowExecutionContract -Content $quotedDuplicateJobsMutation) {
        Add-Failure 'Workflow contract self-test did not reject a quoted duplicate jobs key.'
    }

    $inlineDuplicateJobsMutation = $content + "`njobs: {}`n"
    if (Test-WorkflowExecutionContract -Content $inlineDuplicateJobsMutation) {
        Add-Failure 'Workflow contract self-test did not reject an inline duplicate jobs key.'
    }

    $duplicateStepsMutation = $content + "`n    steps:`n"
    if (Test-WorkflowExecutionContract -Content $duplicateStepsMutation) {
        Add-Failure 'Workflow contract self-test did not reject a duplicate steps key.'
    }

    $rootCommentMutation = $content + "`n# Canonical root comments must not change workflow semantics.`n"
    if (-not (Test-WorkflowExecutionContract -Content $rootCommentMutation)) {
        Add-Failure 'Workflow contract self-test rejected a harmless root comment.'
    }

    $semanticCommentMutation = $content.Replace(
        '    strategy:',
        "    strategy:`n    # A comment does not change the semantic line contract."
    ).Replace(
        "        if: runner.os == 'Windows'",
        "        if: runner.os == 'Windows'`n        # Keep comments valid inside the same step."
    )
    if (
        $semanticCommentMutation -ceq $content -or
        -not (Test-WorkflowExecutionContract -Content $semanticCommentMutation)
    ) {
        Add-Failure 'Workflow contract self-test rejected harmless comments inside validate.'
    }

    $semanticBlankMutation = $content.Replace(
        '    runs-on: ${{ matrix.os }}',
        "`n    runs-on: `${{ matrix.os }}"
    )
    if (
        $semanticBlankMutation -ceq $content -or
        -not (Test-WorkflowExecutionContract -Content $semanticBlankMutation)
    ) {
        Add-Failure 'Workflow contract self-test rejected a harmless blank line inside validate.'
    }

    $trailingJobMutation = $content + "`n  other-job:`n    runs-on: ubuntu-latest`n    steps: []`n"
    if (Test-WorkflowExecutionContract -Content $trailingJobMutation) {
        Add-Failure 'Workflow contract self-test did not reject an unexpected sibling job.'
    }
}

function Test-SkillFrontmatter {
    $skillPath = Get-RepoFilePath -RelativePath 'SKILL.md'
    if (-not (Test-Path -LiteralPath $skillPath -PathType Leaf)) {
        return
    }

    $lines = Get-Content -LiteralPath $skillPath -Encoding UTF8
    if ($lines.Count -lt 4 -or $lines[0] -ne '---') {
        Add-Failure 'SKILL.md must start with YAML frontmatter.'
        return
    }

    $closingIndex = -1
    for ($index = 1; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -eq '---') {
            $closingIndex = $index
            break
        }
    }

    if ($closingIndex -lt 0) {
        Add-Failure 'SKILL.md frontmatter must be closed with --- before content.'
        return
    }

    $frontmatter = $lines[1..($closingIndex - 1)] -join "`n"
    if ($frontmatter -notmatch '(?m)^name:\s*markdown-idempotent-section-merge\s*$') {
        Add-Failure 'SKILL.md frontmatter must declare name: markdown-idempotent-section-merge.'
    }
    if ($frontmatter -notmatch '(?m)^description:\s*\S') {
        Add-Failure 'SKILL.md frontmatter must include a non-empty description.'
    }
    if ($frontmatter.Length -gt 1024) {
        Add-Failure 'SKILL.md frontmatter must stay under 1024 characters.'
    }
}

$fixtureNames = @(
    'append-missing-section',
    'frontmatter-heading-literal',
    'h1-boundary',
    'html-block-heading-literal',
    'replace-existing-section',
    'subheading-boundary',
    'trap-heading-inside-fence'
)

$requiredFiles = @(
    '.editorconfig',
    '.gitattributes',
    '.gitignore',
    '.github/ISSUE_TEMPLATE/bug_report.yml',
    '.github/ISSUE_TEMPLATE/config.yml',
    '.github/pull_request_template.md',
    '.github/workflows/validate.yml',
    'CHANGELOG.md',
    'CODE_OF_CONDUCT.md',
    'CONTRIBUTING.md',
    'LICENSE',
    'README.md',
    'SECURITY.md',
    'SKILL.md',
    'docs/SKILL.ja.md',
    'docs/closing-hash-managed-heading-contract.md',
    'docs/commonmark-ascii-whitespace-contract.md',
    'docs/frontmatter-heading-scan-contract.md',
    'docs/html-block-heading-scan-contract.md',
    'docs/macos-ci-contract.md',
    'examples/before-after.md',
    'examples/verification-recipe.md',
    'docs/private-marker-scanner-hardening.md',
    'docs/setup-python-node24-pin-contract.md',
    'scripts/merge_section.py',
    'scripts/test_merge_section.py',
    'scripts/private-marker-process.ps1',
    'scripts/scan-private-markers.ps1',
    'scripts/test-scan-private-markers.ps1',
    'scripts/validate-oss-readiness.ps1'
)

foreach ($fixtureName in $fixtureNames) {
    foreach ($fixtureFile in @('input.md', 'section.md', 'expected.md')) {
        $requiredFiles += "tests/fixtures/$fixtureName/$fixtureFile"
    }
}

foreach ($requiredFile in $requiredFiles) {
    Assert-FileExists -RelativePath $requiredFile
}

Assert-FileContains -RelativePath 'README.md' -Pattern '(?im)^##\s+Install' -Description 'installation instructions'
Assert-FileContains -RelativePath 'README.md' -Pattern '(?im)^##\s+Validation' -Description 'validation instructions'
Assert-FileContains -RelativePath 'README.md' -Pattern '(?im)^##\s+Contributing' -Description 'contribution guidance'
Assert-FileContains -RelativePath 'README.md' -Pattern '(?im)^##\s+Security' -Description 'security reporting guidance'
Assert-FileContains -RelativePath 'README.md' -Pattern 'CONTRIBUTING\.md' -Description 'link to CONTRIBUTING.md'
Assert-FileContains -RelativePath 'README.md' -Pattern 'SECURITY\.md' -Description 'link to SECURITY.md'
Assert-FileContains -RelativePath 'README.md' -Pattern 'docs/SKILL\.ja\.md' -Description 'link to the Japanese skill version'
Assert-FileContains -RelativePath 'README.md' -Pattern 'merge_section\.py' -Description 'reference implementation usage'
Assert-FileContains -RelativePath 'README.md' -Pattern 'docs/closing-hash-managed-heading-contract\.md' -Description 'link to the closing-hash identity contract'
Assert-FileContains -RelativePath 'README.md' -Pattern 'docs/commonmark-ascii-whitespace-contract\.md' -Description 'link to the CommonMark ASCII whitespace contract'
Assert-FileContains -RelativePath 'README.md' -Pattern '\[the setup-python Node\.js 24 pin contract\]\(docs/setup-python-node24-pin-contract\.md\)' -Description 'exact link to the setup-python Node.js 24 pin contract'
Assert-FileContains -RelativePath 'SKILL.md' -Pattern '(?is)closing-hash block heading.*ambiguous managed-heading.*refuse without writing' -Description 'closing-hash fail-closed contract'
Assert-FileContains -RelativePath 'docs/SKILL.ja.md' -Pattern '(?is)閉じハッシュ形式.*同一性が曖昧.*no-write' -Description 'Japanese closing-hash fail-closed contract'
Assert-FileContains -RelativePath 'SKILL.md' -Pattern '(?is)ASCII-only block whitespace.*NBSP.*EM SPACE.*form feed.*vertical tab' -Description 'CommonMark ASCII-only block whitespace contract'
Assert-FileContains -RelativePath 'docs/SKILL.ja.md' -Pattern '(?is)block whitespace.*ASCII限定.*NBSP.*EM SPACE.*form feed.*vertical tab' -Description 'Japanese CommonMark ASCII-only block whitespace contract'
Assert-FileContains -RelativePath 'SKILL.md' -Pattern '(?is)frontmatter.*YAML.*TOML.*fail' -Description 'frontmatter-aware fail-closed contract'
Assert-FileContains -RelativePath 'docs/SKILL.ja.md' -Pattern '(?is)frontmatter.*YAML.*TOML.*fail' -Description 'Japanese frontmatter-aware fail-closed contract'
Assert-FileContains -RelativePath 'docs/frontmatter-heading-scan-contract.md' -Pattern '(?is)YAML.*TOML.*完全一致.*fail closed' -Description 'frontmatter heading-scan design and test contract'
Assert-FileContains -RelativePath 'docs/closing-hash-managed-heading-contract.md' -Pattern '(?is)CommonMark 0\.31\.2.*closing sequence.*0〜3.*fail closed.*CRLF.*BOM' -Description 'closing-hash managed-heading identity and no-write contract'
Assert-FileContains -RelativePath 'docs/commonmark-ascii-whitespace-contract.md' -Pattern '(?is)CommonMark 0\.31\.2.*NBSP.*EM SPACE.*form feed.*vertical tab.*BOM.*CRLF' -Description 'CommonMark ASCII whitespace design and byte-preservation regressions'
Assert-FileContains -RelativePath 'SKILL.md' -Pattern '(?is)CommonMark 0\.31\.2 raw HTML.*types 1.7.*type 7.*paragraph.*fail' -Description 'raw HTML heading-scan and fail-closed contract'
Assert-FileContains -RelativePath 'docs/SKILL.ja.md' -Pattern '(?is)CommonMark 0\.31\.2 raw HTML.*type 1.7.*type 7.*段落.*fail closed' -Description 'Japanese raw HTML heading-scan and fail-closed contract'
Assert-FileContains -RelativePath 'docs/html-block-heading-scan-contract.md' -Pattern '(?is)type 1.7.*未クローズ.*fail closed.*apply-twice.*CRLF.*BOM' -Description 'raw HTML heading-scan design and regression contract'
Assert-FileContains -RelativePath 'docs/html-block-heading-scan-contract.md' -Pattern '(?is)ASCII.*Unicode case-fold.*end tag' -Description 'ASCII-only raw HTML tag grammar contract'
Assert-FileContains -RelativePath 'docs/html-block-heading-scan-contract.md' -Pattern '(?is)link reference definition.*`===`.*fail closed.*inline HTML' -Description 'reference-definition setext ambiguity contract'
Assert-FileContains -RelativePath '.gitignore' -Pattern '\.private-markers\.local' -Description 'ignore local private marker files'
Assert-FileContains -RelativePath '.editorconfig' -Pattern '(?ms)^\[\*\.ps1\].*?^charset\s*=\s*utf-8-bom\s*$' -Description 'PowerShell UTF-8 BOM compatibility'
Assert-FileContains -RelativePath 'CONTRIBUTING.md' -Pattern '(?im)no token|never.*token|secret' -Description 'secret-safe contribution guidance'
Assert-FileContains -RelativePath 'SECURITY.md' -Pattern '(?im)do not.*public|private|security' -Description 'private vulnerability reporting guidance'
Assert-FileContains -RelativePath 'SECURITY.md' -Pattern '(?is)root-level `\.git` file or\s+directory.*fails closed.*Only nested\s+`\.git` directories and leaf `\.git` files' -Description 'root-versus-nested Git metadata scanner contract'
Assert-FileContains -RelativePath 'docs/private-marker-scanner-hardening.md' -Pattern '(?is)Git probe.*valid worktree.*scan root.*ancestor.*`\.git` file/directory.*fail closed' -Description 'detailed root-level Git metadata failure contract'
Assert-FileContains -RelativePath 'docs/private-marker-scanner-hardening.md' -Pattern 'nested `\.git` directory' -Description 'detailed nested Git directory exclusion contract'
Assert-FileContains -RelativePath 'docs/private-marker-scanner-hardening.md' -Pattern 'leaf `\.git` file' -Description 'detailed leaf Git metadata exclusion contract'
Assert-FileContains -RelativePath 'scripts/test-scan-private-markers.ps1' -Pattern '(?is)invalid-ancestor-git-metadata-.*ancestorMetadataParent.*ancestorScanRoot.*expectedGitMetadataDiagnostic' -Description 'ancestor Git metadata failure regression'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern 'validate-oss-readiness\.ps1' -Description 'OSS readiness validation in CI'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern 'test_merge_section\.py' -Description 'reference implementation tests in CI'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern 'scan-private-markers\.ps1' -Description 'private marker scan in CI'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern 'test-scan-private-markers\.ps1' -Description 'private marker scan self-test in CI'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern '(?m)^\s*uses:\s*actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09(?:\s+#\s*v5)?\s*$' -Description 'exact immutable checkout action revision'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern '(?m)^\s*uses:\s*actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97\s+#\s*v7\.0\.0\s*$' -Description 'exact immutable setup-python action revision'
Assert-WorkflowExecutionContract

Test-SkillFrontmatter

if ($failures.Count -gt 0) {
    Write-Host 'OSS readiness validation failed:'
    foreach ($failure in $failures) {
        Write-Host "- $failure"
    }
    exit 1
}

Write-Host "OSS readiness validation passed for $root"
exit 0
