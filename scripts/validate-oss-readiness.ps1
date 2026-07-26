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
    'docs/frontmatter-heading-scan-contract.md',
    'docs/html-block-heading-scan-contract.md',
    'examples/before-after.md',
    'examples/verification-recipe.md',
    'docs/private-marker-scanner-hardening.md',
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
Assert-FileContains -RelativePath 'SKILL.md' -Pattern '(?is)frontmatter.*YAML.*TOML.*fail' -Description 'frontmatter-aware fail-closed contract'
Assert-FileContains -RelativePath 'docs/SKILL.ja.md' -Pattern '(?is)frontmatter.*YAML.*TOML.*fail' -Description 'Japanese frontmatter-aware fail-closed contract'
Assert-FileContains -RelativePath 'docs/frontmatter-heading-scan-contract.md' -Pattern '(?is)YAML.*TOML.*完全一致.*fail closed' -Description 'frontmatter heading-scan design and test contract'
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
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern '(?m)^\s*uses:\s*actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065(?:\s+#\s*v5)?\s*$' -Description 'exact immutable setup-python action revision'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern 'windows-latest' -Description 'Windows validation runner in CI'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern 'ubuntu-latest' -Description 'Ubuntu validation runner in CI'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern 'timeout-minutes:\s*25' -Description 'bounded CI validation job'
Assert-FileContains -RelativePath '.github/workflows/validate.yml' -Pattern 'shell:\s*powershell' -Description 'Windows PowerShell 5.1 validation in CI'

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
