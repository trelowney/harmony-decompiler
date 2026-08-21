[CmdletBinding()]
param(
    [switch] $Capture,
    [string] $Key,
    [string] $Output,
    [string] $ConcordanceDir
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Get-DefaultConcordanceDirectory {
    # Wherever an ordinary Concordance install puts it, then wherever
    # concordance.exe happens to be on PATH. Nothing here assumes a particular
    # checkout layout; use -ConcordanceDir if yours is somewhere else.
    $candidates = @()
    foreach ($programFiles in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
        if ($programFiles) { $candidates += (Join-Path $programFiles 'Concordance') }
    }
    $onPath = Get-Command 'concordance.exe' -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += (Split-Path -Parent $onPath.Source) }
    $candidates += $PSScriptRoot

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate 'libconcord-6.dll')) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }

    throw ('libconcord-6.dll was not found. Install Concordance, or pass ' +
           '-ConcordanceDir pointing at the directory that contains it. ' +
           'Looked in: ' + ($candidates -join '; '))
}

if (-not $ConcordanceDir) {
    $ConcordanceDir = Get-DefaultConcordanceDirectory
}
$ConcordanceDir = [IO.Path]::GetFullPath($ConcordanceDir)
$libraryPath = Join-Path $ConcordanceDir 'libconcord-6.dll'
if (-not (Test-Path -LiteralPath $libraryPath)) {
    throw "Missing libconcord DLL: $libraryPath"
}

# The Concordance Windows package is 32-bit. Relaunch this same script under
# Windows' 32-bit PowerShell so users do not need 32-bit Python or a compiler.
if ([IntPtr]::Size -ne 4) {
    $powerShell32 = Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $powerShell32)) {
        throw '32-bit Windows PowerShell is unavailable; the bundled libconcord is 32-bit.'
    }

    $forward = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
    if ($Capture) { $forward += '-Capture' }
    if ($PSBoundParameters.ContainsKey('Key')) { $forward += @('-Key', $Key) }
    if ($PSBoundParameters.ContainsKey('Output')) { $forward += @('-Output', $Output) }
    $forward += @('-ConcordanceDir', $ConcordanceDir)
    & $powerShell32 @forward
    exit $LASTEXITCODE
}

[Environment]::CurrentDirectory = $ConcordanceDir

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[UnmanagedFunctionPointer(CallingConvention.Cdecl)]
public delegate void HarmonyLcCallback(
    UInt32 stage,
    UInt32 done,
    UInt32 total,
    UInt32 count,
    UInt32 counterType,
    IntPtr callbackArgument,
    IntPtr stages);

public static class HarmonyIrNative
{
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool SetDllDirectory(string path);

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int init_concord();

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int deinit_concord();

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int get_identity(HarmonyLcCallback callback, IntPtr callbackArgument);

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int get_arch();

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int get_skin();

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int get_fw_ver_maj();

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int get_fw_ver_min();

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int get_usb_vid();

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int get_usb_pid();

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern int learn_from_remote(
        ref UInt32 carrierClock,
        out IntPtr signal,
        ref UInt32 signalLength,
        HarmonyLcCallback callback,
        IntPtr callbackArgument);

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern void delete_ir_signal(IntPtr signal);

    [DllImport("libconcord-6.dll", CallingConvention = CallingConvention.Cdecl)]
    public static extern IntPtr lc_strerror(int error);
}
'@

if (-not [HarmonyIrNative]::SetDllDirectory($ConcordanceDir)) {
    throw "SetDllDirectory failed for $ConcordanceDir"
}

function Get-LibConcordError([int] $Code) {
    $pointer = [HarmonyIrNative]::lc_strerror($Code)
    if ($pointer -eq [IntPtr]::Zero) { return "libconcord error $Code" }
    return [Runtime.InteropServices.Marshal]::PtrToStringAnsi($pointer)
}

function Format-HexWord([int] $Value) {
    return ('{0:X4}' -f ($Value -band 0xFFFF))
}

$callback = [HarmonyLcCallback] {
    param($stage, $done, $total, $count, $counterType, $callbackArgument, $stages)
}

$initialized = $false
try {
    $errorCode = [HarmonyIrNative]::init_concord()
    if ($errorCode -ne 0) {
        throw "Harmony remote not found: $(Get-LibConcordError $errorCode) (code $errorCode)"
    }
    $initialized = $true

    $errorCode = [HarmonyIrNative]::get_identity($callback, [IntPtr]::Zero)
    if ($errorCode -ne 0) {
        throw "Could not identify the Harmony remote: $(Get-LibConcordError $errorCode) (code $errorCode)"
    }

    $remote = [ordered]@{
        usb_vid = Format-HexWord ([HarmonyIrNative]::get_usb_vid())
        usb_pid = Format-HexWord ([HarmonyIrNative]::get_usb_pid())
        architecture = [HarmonyIrNative]::get_arch()
        skin = [HarmonyIrNative]::get_skin()
        firmware = ('{0}.{1}' -f [HarmonyIrNative]::get_fw_ver_maj(), [HarmonyIrNative]::get_fw_ver_min())
    }

    Write-Host ('Harmony detected: USB {0}:{1}, arch {2}, skin {3}, firmware {4}' -f
        $remote.usb_vid, $remote.usb_pid, $remote.architecture, $remote.skin, $remote.firmware)

    if (-not $Capture) {
        Write-Host 'Probe completed. No capture command was sent.'
        return
    }

    if (-not $Output) {
        $safeKey = if ($Key) { $Key -replace '[^A-Za-z0-9._-]', '_' } else { 'unnamed' }
        $Output = Join-Path (Get-Location) ('harmony-ir-{0}-{1}.json' -f
            $safeKey, (Get-Date -Format 'yyyyMMdd-HHmmss'))
    }
    $Output = [IO.Path]::GetFullPath($Output)

    Write-Host ''
    Write-Host ('Point the ORIGINAL device remote at the Harmony receiver and briefly press "{0}" now.' -f
        $(if ($Key) { $Key } else { 'the desired key' }))
    Write-Host 'Use a short tap; holding the key can fill libconcord''s 1000-duration buffer.'
    Write-Host 'Waiting up to 5 seconds for IR; 0.5 seconds of silence ends the capture.'

    [uint32] $carrier = 0
    [uint32] $length = 0
    [IntPtr] $signalPointer = [IntPtr]::Zero
    $captureError = 0
    $durations = @()

    try {
        $captureError = [HarmonyIrNative]::learn_from_remote(
            [ref] $carrier,
            [ref] $signalPointer,
            [ref] $length,
            $callback,
            [IntPtr]::Zero)

        # libconcord sets ir_signal_length to 0 and allocates ir_signal before
        # anything in its capture loop can fail, so a non-zero pointer here is
        # always valid for exactly $length words, error or not. On the two
        # early returns that never reach the loop the pointer stays null,
        # because the CLR zeroes an `out` parameter.
        if ($signalPointer -ne [IntPtr]::Zero -and $length -gt 0) {
            # Read into a fixed array. Appending to a PowerShell array
            # reallocates on every element, which is half a million copies at
            # the 1000-duration limit.
            $buffer = [int[]]::new($length)
            [Runtime.InteropServices.Marshal]::Copy($signalPointer, $buffer, 0, $length)
            # Captures are bounded to five seconds, so every duration fits in Int32.
            $durations = $buffer
        }
    }
    finally {
        if ($signalPointer -ne [IntPtr]::Zero) {
            [HarmonyIrNative]::delete_ir_signal($signalPointer)
        }
    }

    if ($captureError -ne 0 -and $durations.Count -eq 0) {
        throw "IR capture failed: $(Get-LibConcordError $captureError) (code $captureError)"
    }

    $warnings = @()
    if ($captureError -ne 0) {
        $warnings += "libconcord returned code $captureError after receiving data: $(Get-LibConcordError $captureError)"
    }
    if ($durations.Count -ge 1000) {
        $warnings += 'Capture reached libconcord''s 1000-duration limit and may be truncated.'
    }
    if (($durations.Count % 2) -ne 0) {
        $warnings += 'Duration count is odd although libconcord documents mark/space pairs.'
    }

    $result = [ordered]@{
        schema = 'harmony-ir-capture/v1'
        captured_at_utc = [DateTime]::UtcNow.ToString('o')
        key = $Key
        source = [ordered]@{
            backend = 'Concordance libconcord learn_from_remote'
            library = [IO.Path]::GetFileName($libraryPath)
            library_sha256 = (Get-FileHash -LiteralPath $libraryPath -Algorithm SHA256).Hash
            remote = $remote
        }
        waveform = [ordered]@{
            carrier_hz = [uint32] $carrier
            duration_unit = 'microseconds'
            starts_with = 'mark'
            alternates = 'mark,space'
            duration_count = $durations.Count
            durations_us = $durations
        }
        capture = [ordered]@{
            libconcord_error = $captureError
            partial = ($captureError -ne 0)
            truncated = ($durations.Count -ge 1000)
            warnings = $warnings
        }
    }

    $parent = [IO.Path]::GetDirectoryName($Output)
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        [void] [IO.Directory]::CreateDirectory($parent)
    }
    $json = $result | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($Output, $json, (New-Object Text.UTF8Encoding($false)))

    Write-Host ('Captured {0} durations at {1} Hz.' -f $durations.Count, $carrier)
    foreach ($warning in $warnings) { Write-Warning $warning }
    Write-Host "Saved: $Output"
}
finally {
    if ($initialized) {
        [void] [HarmonyIrNative]::deinit_concord()
    }
}
