# Discover the running Daedelus Studio shell and its backend sidecar(s), their listening
# ports and health. Prints JSON. Never touches processes other than daedelus-*.
#   sidecar_probe.ps1 [-Out file.json]
param([string]$Out)

function Scrub([string]$s) { if ($s) { $s.Replace($env:USERPROFILE, '%USERPROFILE%') } else { $s } }

$procs = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'daedelus-studio.exe', 'daedelus-server.exe' })
$listen = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue)
$rows = foreach ($p in $procs) {
    $gp = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
    $ports = @($listen | Where-Object OwningProcess -eq $p.ProcessId | ForEach-Object { "$($_.LocalAddress):$($_.LocalPort)" })
    [ordered]@{
        pid = $p.ProcessId; parent = $p.ParentProcessId; name = $p.Name
        cmdline = Scrub $p.CommandLine; exe = Scrub $p.ExecutablePath
        started = $(if ($gp) { $gp.StartTime.ToString('s') } else { $null })
        working_set_mb = $(if ($gp) { [math]::Round($gp.WorkingSet64 / 1MB, 1) } else { $null })
        listening = $ports
    }
}
$webview = @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" | Where-Object { $_.CommandLine -match 'org\.daedelus\.studio|daedelus-studio' })
$health = $null; $url = $null
foreach ($r in $rows) {
    foreach ($l in $r.listening) {
        $port = $l.Split(':')[-1]
        try {
            $h = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 5
            $url = "http://127.0.0.1:$port"; $health = $h; break
        } catch {}
    }
    if ($health) { break }
}
$result = [ordered]@{
    captured = (Get-Date).ToString('s')
    processes = @($rows)
    webview2_processes = $webview.Count
    webview2_mb = [math]::Round((($webview | ForEach-Object { (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).WorkingSet64 } | Measure-Object -Sum).Sum) / 1MB, 1)
    backend_url = $url
    health = $(if ($health) { [ordered]@{
                ok = $health.ok; version = $health.version; workspace = (Scrub $health.workspace)
                adapters = @($health.adapters | ForEach-Object { [ordered]@{ name = $_.name; available = $_.available; reason = $_.unavailable_reason; environment = $_.environment } })
            } } else { $null })
}
$json = ($result | ConvertTo-Json -Depth 6).Replace($env:USERPROFILE.Replace('\', '\\'), '%USERPROFILE%').Replace($env:USERPROFILE.Replace('\', '/'), '%USERPROFILE%')
if ($Out) { New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null; $json | Set-Content -Encoding utf8 $Out }
$json
