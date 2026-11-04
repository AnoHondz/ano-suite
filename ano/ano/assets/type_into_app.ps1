# Ano scheduled action: bring an app to the front, type exact text, press Enter. Prints one result line.
param([Parameter(Mandatory)][string]$Process, [Parameter(Mandatory)][string]$StartName,
      [Parameter(Mandatory)][string]$Text, [switch]$DryRun)

Add-Type @'
using System; using System.Runtime.InteropServices;
public static class AnoWin {
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
}
'@

function Find-App { Get-Process -Name $Process -ErrorAction SilentlyContinue | Where-Object MainWindowHandle -ne 0 | Select-Object -First 1 }

$app = Find-App
if (-not $app) {
    $entry = Get-StartApps | Where-Object Name -match $StartName | Select-Object -First 1
    if (-not $entry) { 'FAILED: the app is not open and could not be found to start it. Nothing was typed.'; exit 1 }
    Start-Process "shell:AppsFolder\$($entry.AppID)"
    for ($i = 0; $i -lt 30 -and -not ($app = Find-App); $i++) { Start-Sleep 1 }
    if (-not $app) { 'FAILED: the app did not open within 30 seconds. Nothing was typed.'; exit 1 }
    Start-Sleep 5  # let its last page load
}

$handle = $app.MainWindowHandle
[AnoWin]::ShowWindow($handle, 9) | Out-Null          # restore if minimised
[AnoWin]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)   # a tap of Alt lets Windows hand over focus
[AnoWin]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
[AnoWin]::SetForegroundWindow($handle) | Out-Null
Start-Sleep 1
if ([AnoWin]::GetForegroundWindow() -ne $handle) { 'FAILED: could not bring the app to the front. Nothing was typed.'; exit 1 }
if ($DryRun) { 'OK: dry run, the app is in front. Nothing was typed.'; exit 0 }

# SendKeys treats + ^ % ~ ( ) { } [ ] as commands; brace them so the text is typed literally.
$literal = [regex]::Replace($Text, '[+^%~(){}\[\]]', '{$0}')
$shell = New-Object -ComObject WScript.Shell
$shell.SendKeys($literal)
Start-Sleep -Milliseconds 300
if ([AnoWin]::GetForegroundWindow() -ne $handle) { 'FAILED: the app lost focus while typing, so Enter was not pressed.'; exit 1 }
$shell.SendKeys('{ENTER}')
'OK: typed the text and pressed Enter.'
