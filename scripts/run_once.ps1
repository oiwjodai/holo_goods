param(
  [string]$RepoRoot = (Split-Path -Parent $MyInvocation.MyCommand.Path)
)

# プロジェクト直下へ移動
Set-Location (Resolve-Path (Join-Path $RepoRoot '..'))

# .env を読み込んで環境変数に反映
$envPath = Join-Path (Get-Location) '.env'
if (Test-Path $envPath) {
  (Get-Content $envPath) | ForEach-Object {
    if ($_ -match '^(\s*#|\s*$)') { return }
    $kv = $_.Split('=', 2)
    if ($kv.Length -eq 2) {
      [System.Environment]::SetEnvironmentVariable($kv[0], $kv[1])
    }
  }
}

# venv /.venv を優先的に利用
$pyCandidates = @(
  Join-Path (Get-Location) 'venv\Scripts\python.exe',
  Join-Path (Get-Location) '.venv\Scripts\python.exe',
  'python'
)

$py = $pyCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

& $py -m holo_monitor.runner

exit $LASTEXITCODE
