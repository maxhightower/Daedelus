# Capture current-user/system state that an installer could touch, or diff two captures.
#   system_snapshot.ps1 -Dir <snapdir> -Tag pre-nsis
#   system_snapshot.ps1 -Dir <snapdir> -Diff pre-nsis,post-nsis
# Output is text lists; diffs print only added (+) / removed (-) lines.
param([Parameter(Mandatory = $true)][string]$Dir, [string]$Tag, [string[]]$Diff)

$kinds = 'uninstall', 'shortcuts', 'dirs', 'hkcu-software', 'run', 'tasks', 'services', 'userpath', 'firewall'
if ($Diff) {
    $Diff = @($Diff | ForEach-Object { $_ -split ',' })  # -File passes "a,b" as one string
    foreach ($k in $kinds) {
        $a = "$Dir\$($Diff[0])-$k.txt"; $b = "$Dir\$($Diff[1])-$k.txt"
        if (-not ((Test-Path $a) -and (Test-Path $b))) { continue }
        $d = Compare-Object (Get-Content $a) (Get-Content $b)
        foreach ($l in $d) { '{0} [{1}] {2}' -f $(if ($l.SideIndicator -eq '=>') { '+' } else { '-' }), $k, $l.InputObject }
    }
    return
}
New-Item -ItemType Directory -Force $Dir | Out-Null
$roots = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
Get-ChildItem $roots -ErrorAction SilentlyContinue | ForEach-Object Name | Sort-Object | Set-Content "$Dir\$Tag-uninstall.txt"
# Start menus recursively; desktops top-level only (a OneDrive-backed desktop can hold large trees).
@(Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs", "$env:ProgramData\Microsoft\Windows\Start Menu\Programs" -Recurse -ErrorAction SilentlyContinue) + @(Get-ChildItem ([Environment]::GetFolderPath('Desktop')), "$env:PUBLIC\Desktop" -ErrorAction SilentlyContinue) | ForEach-Object FullName | Sort-Object | Set-Content "$Dir\$Tag-shortcuts.txt"
Get-ChildItem $env:LOCALAPPDATA, $env:APPDATA, "$env:LOCALAPPDATA\Programs", 'C:\Program Files', 'C:\Program Files (x86)' -Directory -ErrorAction SilentlyContinue | ForEach-Object FullName | Sort-Object | Set-Content "$Dir\$Tag-dirs.txt"
Get-ChildItem HKCU:\Software -ErrorAction SilentlyContinue | ForEach-Object Name | Sort-Object | Set-Content "$Dir\$Tag-hkcu-software.txt"
(Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Run -ErrorAction SilentlyContinue | Out-String) -split "`r?`n" | Where-Object { $_ -and $_ -notmatch '^PS' } | Set-Content "$Dir\$Tag-run.txt"
Get-ScheduledTask -ErrorAction SilentlyContinue | ForEach-Object { $_.TaskPath + $_.TaskName } | Sort-Object | Set-Content "$Dir\$Tag-tasks.txt"
Get-Service | ForEach-Object Name | Sort-Object | Set-Content "$Dir\$Tag-services.txt"
([Environment]::GetEnvironmentVariable('Path', 'User') -split ';') | Set-Content "$Dir\$Tag-userpath.txt"
Get-NetFirewallRule -ErrorAction SilentlyContinue | ForEach-Object DisplayName | Sort-Object | Set-Content "$Dir\$Tag-firewall.txt"
"snapshot $Tag -> $Dir"
