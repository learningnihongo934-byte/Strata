# Strata Agent: a tiny HTTP service inside Windows that the Mac talks to (through a localhost-only
# port forward) to run commands, sync the clipboard, map the shared folder and open apps.
$ErrorActionPreference = "Stop"
$Version = "1.0.0"
$dir = "C:\ProgramData\Strata"
$cfg = Get-Content "$dir\config.json" -Raw | ConvertFrom-Json
Add-Type -AssemblyName System.Windows.Forms

function Log($msg) { Add-Content "$dir\agent.log" "$(Get-Date -Format s) $msg" -ErrorAction SilentlyContinue }

function Map-Share {
    if (Test-Path "Z:\") { return $true }
    cmd /c "net use Z: \\10.0.2.4\qemu /persistent:no >nul 2>&1"
    if (Test-Path "Z:\") {
        # Give the drive a friendly name in File Explorer.
        $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2\##10.0.2.4#qemu"
        New-Item $key -Force | Out-Null
        Set-ItemProperty $key -Name _LabelFromReg -Value "Mac Shared Folder"
        return $true
    }
    return $false
}

function Invoke-Command2($cmd, $shell, $cwd, $timeoutSec) {
    $marker = "::STRATA_CWD::"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $psi.StandardErrorEncoding = [Text.Encoding]::UTF8
    if ($cwd -and (Test-Path -LiteralPath $cwd -PathType Container)) { $psi.WorkingDirectory = $cwd }
    else { $psi.WorkingDirectory = $env:USERPROFILE }
    if ($shell -eq "powershell") {
        $script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; `$ProgressPreference='SilentlyContinue'`n" +
                  "$cmd`n" + "Write-Output '$marker'; (Get-Location).Path"
        $psi.FileName = "powershell.exe"
        $psi.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " +
            [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($script))
    } else {
        $psi.FileName = "cmd.exe"
        $psi.Arguments = "/d /s /c `"chcp 65001 >nul & $cmd & echo $marker& cd`""
    }
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    $timedOut = -not $p.WaitForExit($timeoutSec * 1000)
    if ($timedOut) { try { $p.Kill() } catch {} ; $p.WaitForExit(2000) | Out-Null }
    $stdout = $out.Result
    $newCwd = $psi.WorkingDirectory
    $i = $stdout.LastIndexOf($marker)
    if ($i -ge 0) {
        $newCwd = $stdout.Substring($i + $marker.Length).Trim()
        $stdout = $stdout.Substring(0, $i)
    }
    $code = if ($timedOut) { -1 } else { $p.ExitCode }
    return @{ stdout = $stdout; stderr = $err.Result; code = $code; cwd = $newCwd; timed_out = $timedOut }
}

function Reply($ctx, $status, $obj) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress -Depth 5))
    $ctx.Response.StatusCode = $status
    $ctx.Response.ContentType = "application/json; charset=utf-8"
    $ctx.Response.ContentLength64 = $bytes.Length
    $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
    $ctx.Response.Close()
}

function Read-Body($ctx) {
    $reader = New-Object IO.StreamReader($ctx.Request.InputStream, [Text.Encoding]::UTF8)
    $text = $reader.ReadToEnd()
    if ($text) { return $text | ConvertFrom-Json } else { return $null }
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://+:$($cfg.port)/")
while ($true) {
    try { $listener.Start(); break } catch { Log "listener start failed: $_"; Start-Sleep 5 }
}
Log "agent $Version started"
$shareMapped = Map-Share
$lastShareTry = Get-Date

while ($listener.IsListening) {
    $ctx = $listener.GetContext()
    try {
        if ($ctx.Request.Headers["X-Strata-Token"] -ne $cfg.token) { Reply $ctx 403 @{ error = "bad token" }; continue }
        if (-not $shareMapped -and ((Get-Date) - $lastShareTry).TotalSeconds -gt 30) {
            $shareMapped = Map-Share; $lastShareTry = Get-Date
        }
        $path = $ctx.Request.Url.AbsolutePath
        $method = $ctx.Request.HttpMethod
        switch ("$method $path") {
            "GET /ping" {
                Reply $ctx 200 @{ ok = $true; version = $Version; hostname = $env:COMPUTERNAME;
                                  user = $env:USERNAME; share = $shareMapped }
            }
            "GET /info" {
                $os = Get-CimInstance Win32_OperatingSystem
                Reply $ctx 200 @{ os = $os.Caption; build = $os.BuildNumber;
                    uptime_sec = [int]((Get-Date) - $os.LastBootUpTime).TotalSeconds;
                    free_mem_mb = [int]($os.FreePhysicalMemory / 1024);
                    disk_free_gb = [math]::Round((Get-PSDrive C).Free / 1GB, 1); share = $shareMapped }
            }
            "GET /clipboard" {
                $t = [System.Windows.Forms.Clipboard]::GetText()
                Reply $ctx 200 @{ text = $t }
            }
            "POST /clipboard" {
                $b = Read-Body $ctx
                if ($b.text) { [System.Windows.Forms.Clipboard]::SetText([string]$b.text) }
                else { [System.Windows.Forms.Clipboard]::Clear() }
                Reply $ctx 200 @{ ok = $true }
            }
            "POST /run" {
                $b = Read-Body $ctx
                $timeout = if ($b.timeout) { [int]$b.timeout } else { 120 }
                Reply $ctx 200 (Invoke-Command2 ([string]$b.cmd) ([string]$b.shell) ([string]$b.cwd) $timeout)
            }
            "POST /open" {
                $b = Read-Body $ctx
                Start-Process ([string]$b.target)
                Reply $ctx 200 @{ ok = $true }
            }
            "POST /share" {
                $shareMapped = Map-Share
                Reply $ctx 200 @{ share = $shareMapped }
            }
            default { Reply $ctx 404 @{ error = "unknown endpoint" } }
        }
    } catch {
        Log "request error: $_"
        try { Reply $ctx 500 @{ error = "$_" } } catch {}
    }
}
