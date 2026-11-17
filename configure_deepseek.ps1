param(
    [ValidateSet("deepseek-flash", "deepseek-v4-pro")]
    [string]$Model = "deepseek-flash"
)

$ErrorActionPreference = "Stop"
$configPath = Join-Path $env:LOCALAPPDATA "TeamManageSystem/runtime.dpapi"
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "The local project launcher is not configured. See README.md for environment setup."
}

$keyInput = Read-Host "Enter your DeepSeek API key" -AsSecureString
$apiKey = [PSCredential]::new("deepseek", $keyInput).GetNetworkCredential().Password.Trim()
if (-not $apiKey) {
    throw "No API key entered."
}

$body = @{
    model = $Model
    thinking = @{ type = "disabled" }
    response_format = @{ type = "json_object" }
    max_tokens = 100
    messages = @(
        @{ role = "system"; content = 'Return this exact JSON object: {"ready":true}' }
        @{ role = "user"; content = "Test the DeepSeek connection." }
    )
} | ConvertTo-Json -Depth 6 -Compress

try {
    $probe = Invoke-RestMethod -Uri "https://api.deepseek.com/chat/completions" -Method Post -ContentType "application/json" -Headers @{ Authorization = "Bearer $apiKey" } -Body $body -TimeoutSec 30
}
catch {
    if ($_.Exception.Response) {
        throw "DeepSeek returned HTTP $([int]$_.Exception.Response.StatusCode). The existing configuration was kept."
    }
    throw "Could not connect to DeepSeek. The existing configuration was kept."
}
if ($probe.choices[0].finish_reason -ne "stop" -or ($probe.choices[0].message.content | ConvertFrom-Json).ready -ne $true) {
    throw "DeepSeek did not return the expected JSON. The existing configuration was kept."
}

$current = (Get-Content -LiteralPath $configPath -Raw).Trim() | ConvertTo-SecureString
$config = [PSCredential]::new("local", $current).GetNetworkCredential().Password | ConvertFrom-Json
$config | Add-Member -NotePropertyName "NBA_AI_PROVIDER" -NotePropertyValue "deepseek" -Force
$config | Add-Member -NotePropertyName "NBA_AI_MODEL" -NotePropertyValue $Model -Force
$config | Add-Member -NotePropertyName "DEEPSEEK_API_KEY" -NotePropertyValue $apiKey -Force
$suffix = [Guid]::NewGuid().ToString("N")
$temporary = "$configPath.$suffix.tmp"
$backup = "$configPath.$suffix.bak"
try {
    $config | ConvertTo-Json -Depth 8 -Compress | ConvertTo-SecureString -AsPlainText -Force |
        ConvertFrom-SecureString | Set-Content -LiteralPath $temporary
    [IO.File]::Replace($temporary, $configPath, $backup)
}
finally {
    foreach ($file in @($temporary, $backup)) {
        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file }
    }
}
Write-Host "DeepSeek verified and saved locally ($Model). Restart the application to apply the configuration."
