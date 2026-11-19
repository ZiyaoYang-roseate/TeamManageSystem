param(
    [string]$Python
)

$ErrorActionPreference = "Stop"
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not $env:FLASK_SECRET_KEY -or $env:FLASK_SECRET_KEY.Length -lt 32) {
        throw "请设置至少 32 个字符的 FLASK_SECRET_KEY。"
    }
    if (-not $env:MYSQL_PASSWORD) {
        throw "请设置 MYSQL_PASSWORD。"
    }
    if (-not $Python) {
        $Python = @(
            ".\.venv-windows\Scripts\python.exe"
            ".\.venv\Scripts\python.exe"
            ".\.venv\bin\python.exe"
        ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
        if (-not $Python) {
            $Python = "python"
        }
    }
    & $Python app.py
    if ($LASTEXITCODE -ne 0) {
        throw "应用退出，状态码 $LASTEXITCODE。"
    }
}
finally {
    Pop-Location
}
