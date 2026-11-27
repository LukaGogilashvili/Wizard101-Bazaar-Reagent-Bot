param([Parameter(Mandatory=$true)][string]$Executable)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class BuyerTestInput {
    [DllImport("user32.dll", CharSet=CharSet.Unicode)]
    public static extern IntPtr SendMessage(IntPtr hwnd, uint message, IntPtr wParam, string text);
    [DllImport("user32.dll")]
    public static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wParam, IntPtr lParam);
}
'@

function Wait-For($check, $description) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $value = & $check
        if ($value) { return $value }
        Start-Sleep -Milliseconds 100
    } while ($timer.Elapsed.TotalSeconds -lt 30)
    throw "Timed out waiting for $description"
}

function Control($name) {
    $condition = [Windows.Automation.PropertyCondition]::new(
        [Windows.Automation.AutomationElement]::NameProperty, $name)
    $window.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
}

function Press($name) {
    $button = Control $name
    if (-not $button -or -not $button.Current.IsEnabled) { throw "Unavailable control: $name" }
    $null = [BuyerTestInput]::PostMessage([IntPtr]$button.Current.NativeWindowHandle, 0xF5, [IntPtr]::Zero, [IntPtr]::Zero)
}

$exe = (Resolve-Path -LiteralPath $Executable).Path
$scratch = Join-Path ([IO.Path]::GetTempPath()) ('BazaarGuiTest-' + [guid]::NewGuid().ToString('N'))
$null = [IO.Directory]::CreateDirectory($scratch)
$app = $null
try {
    $copy = Join-Path $scratch 'Buyer.exe'
    [IO.File]::Copy($exe, $copy)
    $info = [Diagnostics.ProcessStartInfo]::new($copy)
    $info.WorkingDirectory = $scratch
    $info.UseShellExecute = $false
    $info.EnvironmentVariables['TEMP'] = $scratch
    $info.EnvironmentVariables['TMP'] = $scratch
    $info.EnvironmentVariables['PATH'] = "$env:WINDIR\System32;$env:WINDIR\System32\WindowsPowerShell\v1.0"
    $app = [Diagnostics.Process]::Start($info)
    $handle = Wait-For { $app.Refresh(); if ($app.MainWindowHandle -ne 0) { $app.MainWindowHandle } } 'main window'
    $window = [Windows.Automation.AutomationElement]::FromHandle($handle)
    $field = Wait-For {
        $window.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition) |
            Where-Object { $_.Current.ClassName -like '*EDIT*' } | Select-Object -First 1
    } 'reagent field'
    $null = [BuyerTestInput]::SendMessage([IntPtr]$field.Current.NativeWindowHandle, 12, [IntPtr]::Zero, '')
    Press 'Start'
    $null = Wait-For { Control 'Enter a reagent name' } 'name validation'
    $null = [BuyerTestInput]::SendMessage([IntPtr]$field.Current.NativeWindowHandle, 12, [IntPtr]::Zero, 'Iron')
    foreach ($mode in 'Scan once','Buy continuously') {
        Press $mode
        Press 'Start'
        $null = Wait-For { Control 'Waiting for F8' } 'packaged worker'
        $null = Wait-For {
            $window.FindAll([Windows.Automation.TreeScope]::Descendants,[Windows.Automation.Condition]::TrueCondition) |
                Where-Object { $_.Current.Name -like "*$($mode.Split(' ')[0])*Iron*Waiting for F8*" -or
                               ($mode -eq 'Buy continuously' -and $_.Current.Name -like '*Continuous buying*Iron*Waiting for F8*') }
        } 'worker log'
        if ($mode -eq 'Scan once') {
            Press 'Stop'
            $null = Wait-For { Control 'Session ended' } 'session cancellation'
        }
    }
    $null = $app.CloseMainWindow()
    if (-not $app.WaitForExit(20000)) { throw 'Window failed to close during a session' }
    if (Get-ChildItem -LiteralPath $scratch -Directory -Filter 'BazaarBuyer-*') {
        throw 'Runtime folder remained after closing'
    }
    Write-Output 'Standalone GUI passed startup, validation, both modes, cancellation, restart, and cleanup.'
} finally {
    if ($app -and -not $app.HasExited) {
        $null = $app.CloseMainWindow()
        if (-not $app.WaitForExit(5000)) { $app.Kill(); $app.WaitForExit() }
    }
    if ((Split-Path -Parent $scratch) -eq [IO.Path]::GetTempPath().TrimEnd('\') -and
        (Split-Path -Leaf $scratch) -match '^BazaarGuiTest-[0-9a-f]{32}$') {
        [IO.Directory]::Delete($scratch, $true)
    }
}
