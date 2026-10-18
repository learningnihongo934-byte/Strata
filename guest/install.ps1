# Strata Tools installer. Runs once, as the auto-logged-in admin user, at Windows' first logon.
$ErrorActionPreference = "Continue"
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$dst = "C:\ProgramData\Strata"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Start-Transcript -Path "$dst\install.log" -Append | Out-Null

Write-Host "Installing Strata Tools..." -ForegroundColor Cyan
Copy-Item "$src\agent.ps1", "$src\config.json" $dst -Force
$cfg = Get-Content "$dst\config.json" -Raw | ConvertFrom-Json

# Drivers: network, display, balloon, serial, input, etc.
Write-Host "Installing drivers (network, display, memory)..."
pnputil /add-driver "$src\drivers\*.inf" /subdirs /install | Out-Null

# Let the agent listen on its port for any user, and allow it through the firewall.
netsh http add urlacl url=http://+:$($cfg.port)/ sddl="D:(A;;GX;;;WD)" | Out-Null
New-NetFirewallRule -DisplayName "Strata Agent" -Direction Inbound -Protocol TCP -LocalPort $cfg.port `
    -Action Allow -Profile Any -ErrorAction SilentlyContinue | Out-Null

# Remote Desktop, for the optional high-resolution "Open in Windows App" mode.
Set-ItemProperty "HKLM:\System\CurrentControlSet\Control\Terminal Server" -Name fDenyTSConnections -Value 0
Enable-NetFirewallRule -DisplayGroup "Remote Desktop" -ErrorAction SilentlyContinue
if ($cfg.blank_password) {
    # Only reachable from the Mac itself (127.0.0.1 port forward), so allow a blank password over RDP.
    Set-ItemProperty "HKLM:\System\CurrentControlSet\Control\Lsa" -Name LimitBlankPasswordUse -Value 0
}
net accounts /maxpwage:unlimited | Out-Null

# Shared folders come from QEMU's built-in SMB server, which uses guest access without signing.
Set-SmbClientConfiguration -EnableInsecureGuestLogons $true -RequireSecuritySignature $false -Confirm:$false -Force
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\LanmanWorkstation\Parameters" `
    -Name AllowInsecureGuestAuth -Value 1 -Type DWord

# A VM shouldn't sleep or lock its screen on its own.
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off

# Run the agent hidden in the user's session at every logon (it needs the session for the clipboard).
$action = New-ScheduledTaskAction -Execute "conhost.exe" `
    -Argument "--headless powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File `"$dst\agent.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "StrataAgent" -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "StrataAgent"

Set-Content "$dst\installed.txt" (Get-Date -Format o)
Write-Host "Strata Tools installed." -ForegroundColor Green
Stop-Transcript | Out-Null
