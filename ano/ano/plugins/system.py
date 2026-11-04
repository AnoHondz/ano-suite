"""PC health: read-only checks run freely; anything that changes the PC waits for the user's click."""
import base64
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time

from PySide6.QtCore import QObject, Property, Signal, Slot, QRunnable, QThreadPool

from ..tools import ToolError

CREATE_NO_WINDOW = 0x08000000
INSPECT_SECONDS = 45
FIX_SECONDS = 1800
MAX_OUTPUT = 12000
# Cmdlets that only read. Anything else (including aliases, & and . invocation) is refused before running.
READ_ONLY = '''
Get-Process Get-Service Get-CimInstance Get-WmiObject Get-WinEvent Get-EventLog Get-NetAdapter
Get-NetAdapterStatistics Get-NetIPConfiguration Get-NetIPAddress Get-NetRoute Get-NetConnectionProfile
Get-NetTCPConnection Get-DnsClientServerAddress Get-DnsClientCache Get-NetFirewallProfile Get-PhysicalDisk
Get-Disk Get-Volume Get-Partition Get-PSDrive Get-ScheduledTask Get-ScheduledTaskInfo Get-HotFix
Get-ComputerInfo Get-Counter Get-PnpDevice Get-PnpDeviceProperty Get-AppxPackage Get-StartApps
Get-ItemProperty Get-ChildItem Get-Item Get-MpComputerStatus Get-MpThreatDetection Get-MpPreference
Get-Printer Get-PrintJob Get-Date Get-TimeZone Get-Culture Get-WindowsOptionalFeature Get-Acl
Get-StorageReliabilityCounter Get-BitLockerVolume Get-TPM Get-Package Get-LocalUser
Test-Connection Test-NetConnection Resolve-DnsName Select-Object Sort-Object Where-Object Group-Object
Measure-Object Format-Table Format-List Format-Wide Out-String ConvertTo-Json ConvertTo-Csv
systeminfo tasklist driverquery whoami
'''.split()
# Casts like [IO.StreamWriter]'C:\x' run constructors that can create files, so only value types may be cast to.
SAFE_CASTS = 'int long double decimal string bool datetime timespan math version int32 int64 uint64 pscustomobject ordered'.split()
SAFE_METHODS = '''Round Floor Ceiling ToString Trim TrimStart TrimEnd Substring ToLower ToUpper Contains StartsWith
EndsWith Split Replace Format AddDays AddHours AddMinutes ToShortDateString ToLocalTime Equals'''.split()
# Resolving a UNC path, a mapped network drive, or a -ComputerName/-CimSession target makes Windows
# connect and authenticate to that server, leaking the user's NTLM credentials. Data-source commands
# therefore only take literal local paths, never remote targets, and never pipeline input built by
# formatting commands (Select-Object can forge a ComputerName or PSPath property).
PIPELINE_ONLY = '''Select-Object Sort-Object Where-Object Group-Object Measure-Object Format-Table Format-List
Format-Wide Out-String ConvertTo-Json ConvertTo-Csv'''.split()
REMOTE_PARAMETERS = 'ComputerName CimSession Session Credential Source InputObject'.split()
PATH_PARAMETERS = 'Path LiteralPath PSPath FilePath Namespace Counter LogName'.split()
# Probes whose ComputerName is the network target itself (ICMP/TCP/DNS; no Windows authentication).
TARGET_COMMANDS = 'Test-Connection Test-NetConnection Resolve-DnsName'.split()
# A probe target can still carry data out in a DNS name (secret.attacker.example), so targets other than
# these well-known diagnostic hosts, loopback and private LAN addresses need the user's approval.
PROBE_PARAMETERS = 'ComputerName Name Server'.split()
PROBE_HOSTS = '''localhost 8.8.8.8 8.8.4.4 1.1.1.1 1.0.0.1 9.9.9.9 dns.google one.one.one.one google.com www.google.com
bing.com www.bing.com microsoft.com www.microsoft.com cloudflare.com www.cloudflare.com internetbeacon.msedge.net
www.msftconnecttest.com'''.split()
LOCAL_PROVIDERS = 'Registry Environment Certificate Variable Function Alias'.split()
INSPECT_SCRIPT = r'''
$ProgressPreference = 'SilentlyContinue'  # module autoload progress would otherwise leak CLIXML into stderr
$command = $env:ANO_INSPECT
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($command, [ref]$tokens, [ref]$errors)
if ($errors.Count) { 'ANO-REFUSED: The command has a syntax error.'; exit }
$allowed = $env:ANO_ALLOWED -split ' '
$methods = $env:ANO_METHODS -split ' '
$casts = $env:ANO_CASTS -split ' '
$pipelineOnly = $env:ANO_PIPELINE_ONLY -split ' '
$remote = $env:ANO_REMOTE -split ' '
$pathParameters = $env:ANO_PATHS -split ' '
$targetCommands = $env:ANO_TARGETS -split ' '
$localProviders = $env:ANO_PROVIDERS -split ' '
$probeParameters = $env:ANO_PROBE_PARAMETERS -split ' '
$probeHosts = $env:ANO_PROBE_HOSTS -split ' '
$unapproved = New-Object System.Collections.Generic.List[string]
$language = 'System.Management.Automation.Language.'
$unc = '^\s*[\\/]{2}'

function Test-ProbeHost([string]$target) {
    if ($probeHosts -contains $target) { return $true }
    $address = $null
    if (-not [System.Net.IPAddress]::TryParse($target, [ref]$address)) { return $false }
    if ([System.Net.IPAddress]::IsLoopback($address) -or $address.IsIPv6LinkLocal) { return $true }
    $b = $address.GetAddressBytes()  # private IPv4 ranges stay on the local network
    return $b.Count -eq 4 -and ($b[0] -eq 10 -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or
        ($b[0] -eq 192 -and $b[1] -eq 168) -or ($b[0] -eq 169 -and $b[1] -eq 254))
}

function Get-Constants($value) {
    # Literal strings only: anything computed could be assembled into a network path at run time.
    if ($value -is ($language + 'StringConstantExpressionAst')) { return ,@($value.Value) }
    if ($value -is ($language + 'ExpandableStringExpressionAst') -and -not $value.NestedExpressions.Count) { return ,@($value.Value) }
    if ($value -is ($language + 'ArrayLiteralAst')) {
        $values = @()
        foreach ($element in $value.Elements) {
            $inner = Get-Constants $element
            if ($null -eq $inner) { return $null }
            $values += $inner
        }
        return ,$values
    }
    return $null
}

function Test-LocalPath([string]$path) {
    if ($path -match $unc -or $path.Contains('::')) { return $false }
    if ($path -notmatch '^([^\\/:]+):') { return $true }  # relative, or rooted on the current local drive
    $drive = $Matches[1]
    if ($drive -notmatch '^[A-Za-z]\w*$') { return $false }
    if ($drive.Length -eq 1) {
        # GetDriveType only; never touches a mapped network drive.
        return @('Fixed', 'Removable', 'CDRom', 'Ram') -contains [string]([System.IO.DriveInfo]::new($drive).DriveType)
    }
    $found = @(Get-PSDrive -Name $drive -ErrorAction SilentlyContinue)
    return $found.Count -eq 1 -and $localProviders -contains $found[0].Provider.Name
}

function Test-DataSource($node, $name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) { return "'$name' is not available on this PC." }
    foreach ($element in $node.CommandElements) {
        if ($element -is ($language + 'VariableExpressionAst') -and $element.Splatted) { return 'Splatting is not allowed.' }
    }
    $binding = [System.Management.Automation.Language.StaticParameterBinder]::BindCommand($node, $true)
    if ($binding.BindingExceptions.Count) { return "The parameters of '$name' could not be checked." }
    foreach ($entry in $binding.BoundParameters.GetEnumerator()) {
        $parameter = $entry.Key
        if ($remote -contains $parameter -and -not ($parameter -eq 'ComputerName' -and $targetCommands -contains $name)) {
            return "-$parameter is not allowed: inspections run on this PC only."
        }
        if ($targetCommands -contains $name -and $probeParameters -contains $parameter) {
            $values = Get-Constants $entry.Value.Value
            if ($null -eq $values) { return "-$parameter must be a literal host name or address." }
            foreach ($value in $values) {
                $target = $value.Trim().TrimEnd('.').ToLowerInvariant()
                if (-not $target -or $target -match '\s') { return "-$parameter must be a single host name or address." }
                if (-not (Test-ProbeHost $target) -and -not $unapproved.Contains($target)) { $unapproved.Add($target) }
            }
        }
        if ($parameter -eq 'FilterXml') { return 'Use -FilterHashtable instead of -FilterXml.' }
        if ($parameter -eq 'FilterHashtable') {
            $table = $entry.Value.Value
            if ($table -isnot ($language + 'HashtableAst')) { return 'Write -FilterHashtable as a literal @{...} table.' }
            foreach ($pair in $table.KeyValuePairs) {
                $key = Get-Constants $pair.Item1
                if ($null -eq $key -or $key -contains 'Path') { return 'Event log files cannot be read through -FilterHashtable.' }
            }
        }
        if ($pathParameters -contains $parameter) {
            $values = Get-Constants $entry.Value.Value
            if ($null -eq $values) { return "-$parameter must be a literal value, not a variable or expression." }
            foreach ($value in $values) {
                if (-not (Test-LocalPath $value)) { return "Network or remote locations are not allowed ($value)." }
            }
        }
    }
    $pipeline = $node.Parent
    $position = $pipeline.PipelineElements.IndexOf($node)
    if ($position -gt 0) {
        $previous = $pipeline.PipelineElements[$position - 1]
        $source = if ($previous -is ($language + 'CommandAst')) { $previous.GetCommandName() } else { $null }
        if (-not $source -or $pipelineOnly -contains $source) {
            return "Pipe into '$name' only directly from another Get- command."
        }
    }
    return $null
}

foreach ($node in $ast.FindAll({ $true }, $true)) {
    $kind = $node.GetType().Name
    if ($kind -eq 'CommandAst') {
        $name = $node.GetCommandName()
        if (-not $name -or $allowed -notcontains $name -or $node.InvocationOperator -ne 'Unknown') {
            "ANO-REFUSED: '$name' is not a read-only command."; exit
        }
        if ($pipelineOnly -notcontains $name) {
            $problem = Test-DataSource $node $name
            if ($problem) { "ANO-REFUSED: $problem"; exit }
        }
    } elseif ($kind -in 'StringConstantExpressionAst', 'ExpandableStringExpressionAst' -and $node.Value -match ($unc + '\w')) {
        'ANO-REFUSED: Network (UNC) paths are not allowed.'; exit
    } elseif ($kind -in 'StringConstantExpressionAst', 'ExpandableStringExpressionAst' -and $node.Value -match 'PingStatus') {
        'ANO-REFUSED: Use Test-Connection to ping; Win32_PingStatus is not allowed.'; exit
    } elseif ($kind -eq 'ConvertExpressionAst' -and $casts -notcontains ($node.Type.TypeName.Name -replace '^System\.', '')) {
        "ANO-REFUSED: Casting to [$($node.Type.TypeName.Name)] is not allowed."; exit
    } elseif ($kind -eq 'InvokeMemberExpressionAst' -and $methods -notcontains $node.Member.Value) {
        "ANO-REFUSED: Calling .$($node.Member) is not allowed."; exit
    } elseif ($kind -eq 'FileRedirectionAst') {
        'ANO-REFUSED: Writing to files is not allowed.'; exit
    }
}
# Checked only after every refusal rule, so an approval is never requested for a command that cannot run.
if ($unapproved.Count) { 'ANO-PROBE: ' + ($unapproved -join ' '); exit }
Invoke-Expression $command | Out-String -Width 180
'''


def encoded(script):
    return base64.b64encode(script.encode('utf-16-le')).decode('ascii')


def powershell(script, timeout, environment=None):
    result = subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded(script)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout,
        creationflags=CREATE_NO_WINDOW, env={**os.environ, **(environment or {})})
    return result.returncode, (result.stdout + ('\n' + result.stderr if result.stderr.strip() else '')).strip()


def clip(text):
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f'\n[Output cut at {MAX_OUTPUT:,} characters.]'


def run_fix(fix):
    """Run an approved fix and return its output. Administrator fixes go through the Windows UAC prompt."""
    command = '$ProgressPreference = "SilentlyContinue"\n' + fix['command']
    if not fix.get('admin'):
        _, output = powershell(command, FIX_SECONDS)
        return clip(output or '(The fix finished with no output.)')
    with tempfile.TemporaryDirectory() as directory:
        script, output = Path(directory) / 'fix.ps1', Path(directory) / 'output.txt'
        target = str(output).replace("'", "''")
        script.write_text(f"& {{\n{command}\n}} *>&1 | Out-File -LiteralPath '{target}' -Encoding utf8 -Width 180\n",
                          encoding='utf-8-sig')
        path = str(script).replace("'", "''")
        code, _ = powershell(
            "Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
            f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"{path}\"'", FIX_SECONDS)
        if code != 0:
            return 'Administrator permission was declined, so nothing was changed.'
        text = output.read_text(encoding='utf-8-sig', errors='replace').strip() if output.exists() else ''
        return clip(text or '(The fix finished with no output.)')


class System:
    def __init__(self, fixes, run=powershell, outbound=None):
        self.fixes = fixes
        self.run_powershell = run
        self.outbound = outbound

    def definitions(self):
        return [
            {'name': 'system_overview',
             'description': 'Snapshot of PC health: CPU and memory use, the busiest programs, disk space, uptime, '
                            'battery, Windows version and whether a restart is pending. Start here for "why is my '
                            'computer slow" and similar questions.',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {}}},
            {'name': 'inspect_system',
             'description': 'Run a READ-ONLY Windows PowerShell 5.1 command to investigate (startup apps, services, '
                            'event-log errors, network, drivers, updates, devices, disk health, Defender status...). '
                            'Only these commands are allowed: ' + ', '.join(READ_ONLY) + '. No aliases, no & or . '
                            'invocation, no file redirection, no method calls except simple string/number ones. '
                            'Not administrator. Prefer focused commands with Select-Object and -First; '
                            f'{INSPECT_SECONDS}-second limit.',
             'input_schema': {'type': 'object', 'additionalProperties': False,
                              'properties': {'command': {'type': 'string'}}, 'required': ['command']}},
            {'name': 'propose_fix',
             'description': 'Offer a fix that changes the PC. Ano shows the user your explanation and the exact '
                            'Windows PowerShell 5.1 command; it only runs if they click Run fix, and the output then '
                            'comes back to you in the chat. Use the smallest safe fix, one at a time. Say what will '
                            'change and how to undo it. Never delete personal files or disable security features. '
                            'Set admin to true only if it truly needs administrator rights (Windows will ask too).',
             'input_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
                 'title': {'type': 'string', 'description': 'Short name, e.g. "Close the frozen Teams process"'},
                 'explanation': {'type': 'string', 'description': 'Plain language: what it does, risks, how to undo'},
                 'command': {'type': 'string'},
                 'admin': {'type': 'boolean'}}, 'required': ['title', 'explanation', 'command']}},
        ]

    def run(self, name, arguments):
        return getattr(self, name)(**arguments)

    def system_overview(self):
        import psutil
        processes = list(psutil.process_iter(['name', 'memory_info']))
        for process in processes:
            try:
                process.cpu_percent(None)
            except psutil.Error:
                pass
        total_cpu = psutil.cpu_percent(interval=1.0)
        cores = psutil.cpu_count() or 1
        rows = []
        for process in processes:
            if process.pid == 0:  # System Idle Process is spare capacity, not a program.
                continue
            try:
                memory = process.info['memory_info'].rss if process.info['memory_info'] else 0
                rows.append((process.cpu_percent(None) / cores, memory, process.info['name'] or '?', process.pid))
            except psutil.Error:
                pass
        memory = psutil.virtual_memory()
        physical = psutil.cpu_count(logical=False)
        lines = [
            f'Windows {platform.release()} (build {platform.version()})',
            f'Processor: {processor_name()} ({physical or "?"} cores, {cores} logical CPUs)',
            f'CPU use: {total_cpu:.0f}%',
            f'Memory: {gib(memory.used)} of {gib(memory.total)} used ({memory.percent:.0f}%)',
            f'Up for: {(time.time() - psutil.boot_time()) / 3600:.1f} hours since last restart',
        ]
        battery = psutil.sensors_battery()
        if battery:
            lines.append(f'Battery: {battery.percent:.0f}%' + (', plugged in' if battery.power_plugged else ', on battery'))
        for partition in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(partition.mountpoint)
                lines.append(f'Disk {partition.mountpoint}: {gib(usage.free)} free of {gib(usage.total)} ({usage.percent:.0f}% used)')
            except OSError:
                pass
        lines.append('Restart pending: ' + ('yes' if restart_pending() else 'no'))
        lines.append('Busiest by CPU: ' + '; '.join(f'{name} (pid {pid}) {cpu:.1f}%' for cpu, _, name, pid
                                                    in sorted(rows, reverse=True)[:8]))
        lines.append('Busiest by memory: ' + '; '.join(f'{name} (pid {pid}) {memory / 2**20:,.0f} MB' for _, memory, name, pid
                                                       in sorted(rows, key=lambda row: row[1], reverse=True)[:8]))
        return '\n'.join(lines), 'Checked CPU, memory, disks and running programs'

    def inspect_system(self, command):
        if not command.strip():
            raise ToolError('The command is empty.')
        output = self._inspect(command, PROBE_HOSTS)
        if output.startswith('ANO-PROBE:'):
            # The script stopped before running anything; ask about the unfamiliar hosts, then run once more.
            hosts = output.removeprefix('ANO-PROBE:').split()
            if self.outbound is None:
                raise ToolError('Network checks are limited to well-known test hosts and local addresses.')
            self.outbound.check_hosts(hosts)
            output = self._inspect(command, PROBE_HOSTS + hosts)
            if output.startswith('ANO-PROBE:'):
                raise ToolError('The network check targets changed. Nothing was run.')
        if output.startswith('ANO-REFUSED:'):
            raise ToolError(output.removeprefix('ANO-REFUSED:').strip() + ' Use propose_fix for changes.')
        summary = ' '.join(command.split())
        return clip(output or '(No output.)'), 'Checked: ' + (summary[:70] + '…' if len(summary) > 70 else summary)

    def _inspect(self, command, probe_hosts):
        try:
            _, output = self.run_powershell(INSPECT_SCRIPT, INSPECT_SECONDS, {
                'ANO_INSPECT': command, 'ANO_ALLOWED': ' '.join(READ_ONLY), 'ANO_METHODS': ' '.join(SAFE_METHODS),
                'ANO_CASTS': ' '.join(SAFE_CASTS), 'ANO_PIPELINE_ONLY': ' '.join(PIPELINE_ONLY),
                'ANO_REMOTE': ' '.join(REMOTE_PARAMETERS), 'ANO_PATHS': ' '.join(PATH_PARAMETERS),
                'ANO_TARGETS': ' '.join(TARGET_COMMANDS), 'ANO_PROVIDERS': ' '.join(LOCAL_PROVIDERS),
                'ANO_PROBE_PARAMETERS': ' '.join(PROBE_PARAMETERS), 'ANO_PROBE_HOSTS': ' '.join(probe_hosts)})
        except subprocess.TimeoutExpired:
            raise ToolError(f'The check took longer than {INSPECT_SECONDS} seconds. Try a narrower command.') from None
        return output

    def propose_fix(self, title, explanation, command, admin=False):
        if not command.strip():
            raise ToolError('The fix has no command.')
        self.fixes.propose({'title': ' '.join(title.split()), 'explanation': explanation.strip(),
                            'command': command.strip(), 'admin': bool(admin)})
        return ('The fix is shown to the user. It has NOT run; it runs only if they click Run fix, and its output '
                'will arrive as their next message.'), f'Suggested a fix: {title}'


def gib(value):
    return f'{value / 2**30:.1f} GB'


def processor_name():
    """The marketing name Windows shows, e.g. "Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz"."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r'HARDWARE\DESCRIPTION\System\CentralProcessor\0') as key:
            return ' '.join(str(winreg.QueryValueEx(key, 'ProcessorNameString')[0]).split())
    except (ImportError, OSError):
        return platform.processor() or 'unknown model'


def restart_pending():
    try:
        import winreg
    except ImportError:
        return False
    keys = (r'SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending',
            r'SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired')
    for key in keys:
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key))
            return True
        except OSError:
            pass
    return False


class FixSignals(QObject):
    completed = Signal(str)


class FixJob(QRunnable):
    def __init__(self, runner, fix):
        super().__init__()
        self.runner = runner
        self.fix = fix
        self.signals = FixSignals()

    def run(self):
        try:
            output = self.runner(self.fix)
        except subprocess.TimeoutExpired:
            output = 'The fix was still running after 30 minutes and was stopped.'
        except Exception:
            output = 'The fix could not be started.'
        self.signals.completed.emit(output)


class Fixes(QObject):
    """Holds one proposed fix until the user runs or cancels it (main thread only)."""
    changed = Signal()
    finished = Signal(str, str)
    cancelled = Signal(str)
    _proposed = Signal('QVariantMap')

    def __init__(self, allowed=lambda: True, runner=run_fix):
        super().__init__()
        self.allowed = allowed
        self.runner = runner
        self._fix = None
        self._job = None
        self.pool = QThreadPool(self)
        # Emitted from the Claude worker thread; delivered to this object on the main thread.
        self._proposed.connect(self._show)

    def propose(self, fix):
        self._proposed.emit(fix)

    @Slot('QVariantMap')
    def _show(self, fix):
        if self._job is None and self.allowed():
            self._fix = dict(fix)
            self.changed.emit()

    @Property(bool, notify=changed)
    def ready(self):
        return self._fix is not None

    @Property(bool, notify=changed)
    def running(self):
        return self._job is not None

    @Property('QVariantMap', notify=changed)
    def fix(self):
        return self._fix or {}

    @Slot()
    def run(self):
        if self._fix is None or self._job is not None or not self.allowed():
            return
        self._job = FixJob(self.runner, self._fix)
        self._job.signals.completed.connect(self._done)
        self.changed.emit()
        self.pool.start(self._job)

    @Slot(str)
    def _done(self, output):
        title = self._job.fix['title']
        self._job = None
        self._fix = None
        self.changed.emit()
        self.finished.emit(title, output)

    @Slot()
    def discard(self):
        if self._job is None and self._fix is not None:
            title = self._fix['title']
            self._fix = None
            self.changed.emit()
            self.cancelled.emit(title)

    def close(self):
        self.pool.waitForDone(5000)
