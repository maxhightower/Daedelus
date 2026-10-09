# NATIVE-1 environment audit. Writes a machine-readable manifest with no serials,
# user names, host names or license data. Usage:
#   powershell -ExecutionPolicy Bypass -File tools/native_validation/env_audit.ps1 -Out evidence/native_1/environment/environment.json
param([Parameter(Mandatory = $true)][string]$Out)

$ErrorActionPreference = 'Continue'
function Scrub([string]$s) {
    if (-not $s) { return $s }
    $s = $s.Replace($env:USERPROFILE, '%USERPROFILE%')
    return $s.Replace($env:USERNAME, '<user>')
}
function Tool($name, [string[]]$versionArgs) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $cmd) { return @{ present = $false } }
    $v = $null
    try { $v = (& $cmd.Source @versionArgs 2>&1 | Select-Object -First 1) -as [string] } catch {}
    return @{ present = $true; path = (Scrub $cmd.Source); version = (Scrub $v) }
}

$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
Add-Type -AssemblyName System.Windows.Forms

$gpus = @(Get-CimInstance Win32_VideoController | ForEach-Object {
    @{ name = $_.Name; driver = $_.DriverVersion; mode = "$($_.CurrentHorizontalResolution)x$($_.CurrentVerticalResolution)@$($_.CurrentRefreshRate)Hz" }
})
$nv = $null
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $row = (nvidia-smi --query-gpu=name,driver_version,memory.total,memory.used --format=csv,noheader,nounits) -split ',\s*'
    $nv = @{ name = $row[0]; driver = $row[1]; vram_total_mib = [int]$row[2]; vram_used_mib_at_audit = [int]$row[3] }
}

# Per-monitor DPI via GetDpiForMonitor.
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public static class Dpi {
  [DllImport("shcore.dll")] public static extern int GetDpiForMonitor(IntPtr h, int t, out uint x, out uint y);
  [DllImport("user32.dll")] public static extern IntPtr MonitorFromPoint(System.Drawing.Point p, uint f);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@ -ReferencedAssemblies System.Drawing -ErrorAction SilentlyContinue
[Dpi]::SetProcessDPIAware() | Out-Null
$screens = @([System.Windows.Forms.Screen]::AllScreens | ForEach-Object {
    $h = [Dpi]::MonitorFromPoint((New-Object System.Drawing.Point ($_.Bounds.X + 1), ($_.Bounds.Y + 1)), 2)
    $dx = 0; $dy = 0; [Dpi]::GetDpiForMonitor($h, 0, [ref]$dx, [ref]$dy) | Out-Null
    @{ bounds = "$($_.Bounds.Width)x$($_.Bounds.Height)"; origin = "$($_.Bounds.X),$($_.Bounds.Y)"; primary = $_.Primary; dpi = $dx; scale_pct = [int]($dx * 100 / 96) }
})

$wv = (Get-ItemProperty 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}' -ErrorAction SilentlyContinue).pv
$wvUser = (Get-ItemProperty 'HKCU:\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}' -ErrorAction SilentlyContinue).pv

$blender = @(Get-ChildItem 'C:\Program Files\Blender Foundation\Blender *\blender.exe' -ErrorAction SilentlyContinue | ForEach-Object {
    @{ path = $_.FullName; version = (& $_.FullName --version 2>$null | Select-Object -First 1) }
})
$officeRoot = 'C:\Program Files\Microsoft Office\root\Office16'
$office = @{}
foreach ($exe in 'EXCEL.EXE', 'WINWORD.EXE', 'POWERPNT.EXE') {
    $f = Join-Path $officeRoot $exe
    $office[$exe] = if (Test-Path $f) { (Get-Item $f).VersionInfo.ProductVersion } else { $null }
}
$lo = @('C:\Program Files\LibreOffice\program\soffice.exe') | Where-Object { Test-Path $_ }

$pythons = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $pythons = @(py -0p 2>$null | ForEach-Object { Scrub $_.Trim() }) }
$pyAlias = Get-Command python -ErrorAction SilentlyContinue
$pyIsStoreStub = $pyAlias -and $pyAlias.Source -like '*WindowsApps*'

$mp = Get-MpComputerStatus -ErrorAction SilentlyContinue
$manifest = [ordered]@{
    schema = 'daedelus.native_1.environment/1'
    captured_utc = (Get-Date).ToUniversalTime().ToString('s') + 'Z'
    windows = [ordered]@{
        caption = $os.Caption; version = $os.Version; build = $os.BuildNumber; arch = $os.OSArchitecture
        webview2_runtime_machine = $wv; webview2_runtime_user = $wvUser
        defender_realtime = $(if ($mp) { $mp.RealTimeProtectionEnabled } else { $null })
        smartscreen = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer' -ErrorAction SilentlyContinue).SmartScreenEnabled
        execution_policy_effective = (Get-ExecutionPolicy).ToString()
        long_paths_enabled = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem' -ErrorAction SilentlyContinue).LongPathsEnabled
        system_drive_free_gb = [math]::Round((Get-PSDrive C).Free / 1GB, 1)
    }
    hardware = [ordered]@{
        cpu = $cpu.Name.Trim(); cores = $cpu.NumberOfCores; threads = $cpu.NumberOfLogicalProcessors
        ram_gb = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1)
        gpus = $gpus; nvidia = $nv; displays = $screens
    }
    software = [ordered]@{
        blender = $blender
        python_launcher = $pythons
        python_on_path_is_store_stub = [bool]$pyIsStoreStub
        git = (Tool git @('--version')); node = (Tool node @('--version')); cargo = (Tool cargo @('--version'))
        rustc = (Tool rustc @('--version')); gh = (Tool gh @('--version'))
        libreoffice = $(if ($lo) { @{ present = $true; path = $lo } } else { @{ present = $false } })
        microsoft_office = $office
        presentmon = (Tool PresentMon @('--version')); wpr = (Tool wpr @('-help'))
    }
}
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$manifest | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $Out
Write-Output "wrote $Out"
