param(
    [switch]$CheckOnly
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Write-Step {
    param([int]$Number, [string]$Message)
    Write-Host ''
    Write-Host ('[{0}/7] {1}' -f $Number, $Message) -ForegroundColor Cyan
}

function Add-PythonCandidate {
    param(
        [System.Collections.Generic.List[object]]$Candidates,
        [hashtable]$Seen,
        [string]$Path,
        [string[]]$PrefixArguments = @(),
        [string]$Source
    )
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return
    }
    $resolvedPath = (Get-Item -LiteralPath $Path).FullName
    # Avoid launching the Microsoft Store from the python.exe app execution alias.
    if ($resolvedPath -match '\\Microsoft\\WindowsApps\\python(?:3(?:\.14)?)?\.exe$') {
        return
    }
    $candidateKey = $resolvedPath + '|' + ($PrefixArguments -join ' ')
    if (-not $Seen.ContainsKey($candidateKey)) {
        $Seen[$candidateKey] = $true
        $Candidates.Add([pscustomobject]@{
            Path = $resolvedPath
            PrefixArguments = $PrefixArguments
            Source = $Source
        })
    }
}

function Test-Python314 {
    param(
        [string]$Path,
        [string[]]$PrefixArguments = @(),
        [string]$Source = 'Python 3.14'
    )
    $probeCode = "import json, platform, struct, sys; print(json.dumps({'major': sys.version_info.major, 'minor': sys.version_info.minor, 'version': '.'.join(map(str, sys.version_info[:3])), 'executable': sys.executable, 'bits': struct.calcsize('P') * 8, 'machine': platform.machine()}))"
    $probeArguments = @($PrefixArguments) + @('-I', '-S', '-c', $probeCode)
    $savedPreference = $ErrorActionPreference
    $savedAutomaticInstall = [System.Environment]::GetEnvironmentVariable('PYTHON_MANAGER_AUTOMATIC_INSTALL', 'Process')
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        # New Python install managers may install a runtime on their first launch.
        # Detection must only inspect already installed runtimes, including in CheckOnly.
        [System.Environment]::SetEnvironmentVariable('PYTHON_MANAGER_AUTOMATIC_INSTALL', 'false', 'Process')
        $probeOutput = & $Path @probeArguments 2>$null
        $probeExitCode = $LASTEXITCODE
        if ($probeExitCode -ne 0) {
            return $null
        }
        $probe = ($probeOutput -join "`n") | ConvertFrom-Json -ErrorAction Stop
        if ($probe.major -eq 3 -and $probe.minor -eq 14 -and (Test-Path -LiteralPath $probe.executable -PathType Leaf)) {
            return [pscustomobject]@{
                Executable = [string]$probe.executable
                Version = [string]$probe.version
                Source = $Source
                Bits = [int]$probe.bits
                Machine = [string]$probe.machine
            }
        }
    } catch {
        return $null
    } finally {
        [System.Environment]::SetEnvironmentVariable('PYTHON_MANAGER_AUTOMATIC_INSTALL', $savedAutomaticInstall, 'Process')
        $ErrorActionPreference = $savedPreference
    }
    return $null
}

function Find-Python314 {
    $candidates = New-Object 'System.Collections.Generic.List[object]'
    $seen = @{}
    Add-PythonCandidate -Candidates $candidates -Seen $seen -Path (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') -Source 'existing .venv'

    $registryRoots = @(
        'HKCU:\Software\Python\PythonCore',
        'HKCU:\Software\WOW6432Node\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )
    foreach ($registryRoot in $registryRoots) {
        if (-not (Test-Path -LiteralPath $registryRoot)) {
            continue
        }
        foreach ($versionKey in @(Get-ChildItem -LiteralPath $registryRoot | Sort-Object PSChildName)) {
            if ($versionKey.PSChildName -notmatch '^3\.14(?:$|[-.t])') {
                continue
            }
            $installKeyPath = Join-Path $versionKey.PSPath 'InstallPath'
            if (Test-Path -LiteralPath $installKeyPath) {
                $installKey = Get-Item -LiteralPath $installKeyPath
                Add-PythonCandidate -Candidates $candidates -Seen $seen -Path ([string]$installKey.GetValue('ExecutablePath')) -Source 'Python registry entry'
                $installDirectory = [string]$installKey.GetValue('')
                if (-not [string]::IsNullOrWhiteSpace($installDirectory)) {
                    Add-PythonCandidate -Candidates $candidates -Seen $seen -Path (Join-Path $installDirectory 'python.exe') -Source 'Python registry entry'
                }
            }
        }
    }

    foreach ($installRoot in @($env:LOCALAPPDATA, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if ([string]::IsNullOrWhiteSpace($installRoot)) {
            continue
        }
        foreach ($relativePath in @('Programs\Python\Python314\python.exe', 'Python314\python.exe')) {
            Add-PythonCandidate -Candidates $candidates -Seen $seen -Path (Join-Path $installRoot $relativePath) -Source 'known Python install location'
        }
    }
    foreach ($commandName in @('python3.14.exe', 'python.exe', 'python3.exe')) {
        foreach ($pythonCommand in @(Get-Command $commandName -CommandType Application -All -ErrorAction SilentlyContinue)) {
            Add-PythonCandidate -Candidates $candidates -Seen $seen -Path $pythonCommand.Source -Source 'Python on PATH'
        }
    }
    foreach ($launcherCommand in @(Get-Command 'py.exe' -CommandType Application -All -ErrorAction SilentlyContinue)) {
        Add-PythonCandidate -Candidates $candidates -Seen $seen -Path $launcherCommand.Source -PrefixArguments @('-3.14') -Source 'Python launcher'
    }

    $fallbackPython = $null
    foreach ($candidate in $candidates) {
        $python = Test-Python314 -Path $candidate.Path -PrefixArguments $candidate.PrefixArguments -Source $candidate.Source
        if ($null -ne $python) {
            if ($python.Bits -eq 64 -and $python.Machine -eq 'AMD64') {
                return $python
            }
            if ($null -eq $fallbackPython) {
                $fallbackPython = $python
            }
        }
    }
    return $fallbackPython
}

function Find-Chrome {
    $chromePaths = New-Object 'System.Collections.Generic.List[string]'
    foreach ($installRoot in @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)) {
        if (-not [string]::IsNullOrWhiteSpace($installRoot)) {
            $chromePaths.Add((Join-Path $installRoot 'Google\Chrome\Application\chrome.exe'))
        }
    }
    foreach ($appKeyPath in @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'
    )) {
        if (Test-Path -LiteralPath $appKeyPath) {
            $registeredPath = [string](Get-Item -LiteralPath $appKeyPath).GetValue('')
            if (-not [string]::IsNullOrWhiteSpace($registeredPath)) {
                $chromePaths.Add($registeredPath.Trim('"'))
            }
        }
    }
    foreach ($chromeCommand in @(Get-Command 'chrome.exe' -CommandType Application -All -ErrorAction SilentlyContinue)) {
        $chromePaths.Add($chromeCommand.Source)
    }
    foreach ($chromePath in $chromePaths) {
        if (Test-Path -LiteralPath $chromePath -PathType Leaf) {
            return (Get-Item -LiteralPath $chromePath).FullName
        }
    }
    return $null
}

function Write-ManualInstallLinks {
    Write-Host ''
    Write-Host 'Official installers:' -ForegroundColor Yellow
    Write-Host '  Python 3.14 for Windows: https://www.python.org/downloads/windows/'
    Write-Host '  Google Chrome:          https://www.google.com/chrome/'
    Write-Host '  App Installer / winget: https://apps.microsoft.com/detail/9NBLGGH4NNS1'
    Write-Host 'After installing the missing program, close this window and run setup_new_pc.cmd again.'
}

function Install-WingetPackage {
    param([string]$PackageId, [string]$DisplayName, [switch]$UserScope)
    $wingetCommand = Get-Command 'winget.exe' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $wingetCommand) {
        Write-ManualInstallLinks
        throw ('{0} is missing and winget is unavailable. Install it using the official link above.' -f $DisplayName)
    }
    $installArguments = @('install', '--id', $PackageId, '--exact', '--source', 'winget')
    if ($UserScope) {
        $installArguments += @('--scope', 'user')
    }
    $installArguments += @('--accept-package-agreements', '--accept-source-agreements')
    Write-Host ('Installing {0} through winget. Follow any Windows installer prompts.' -f $DisplayName)
    & $wingetCommand.Source @installArguments
    $installExitCode = $LASTEXITCODE
    if ($installExitCode -ne 0 -and $installExitCode -ne 3010) {
        Write-ManualInstallLinks
        throw ('winget could not install {0} (exit code {1}). Use its official installer above, then run setup again.' -f $DisplayName, $installExitCode)
    }
    if ($installExitCode -eq 3010) {
        Write-Host 'The installer requested a Windows restart. If detection fails, restart and run setup again.' -ForegroundColor Yellow
    }
}

function Invoke-CheckedCommand {
    param([string]$Executable, [string[]]$Arguments, [string]$FailureMessage)
    & $Executable @Arguments
    $commandExitCode = $LASTEXITCODE
    if ($commandExitCode -ne 0) {
        throw ('{0} (exit code {1}).' -f $FailureMessage, $commandExitCode)
    }
}

try {
    Set-Location -LiteralPath $PSScriptRoot
    Write-Host 'Browser agent: setup for this Windows PC' -ForegroundColor Green
    Write-Step 1 'Check Python 3.14 and Google Chrome'
    $python = Find-Python314
    $chrome = Find-Chrome
    if ($null -ne $python) {
        Write-Host ('Python {0}, {1}-bit {2}: {3} ({4})' -f $python.Version, $python.Bits, $python.Machine, $python.Executable, $python.Source)
    } else {
        Write-Host 'Python 3.14: missing' -ForegroundColor Yellow
    }
    if ($null -ne $chrome) {
        Write-Host ('Chrome: {0}' -f $chrome)
    } else {
        Write-Host 'Google Chrome: missing' -ForegroundColor Yellow
    }
    if ($CheckOnly) {
        Write-Host ''
        Write-Host 'CheckOnly completed. No packages, settings, or keys were changed.'
        if ($null -eq $python -or $null -eq $chrome) {
            exit 1
        }
        exit 0
    }

    Write-Step 2 'Ensure Python 3.14 is installed'
    if ($null -eq $python) {
        Install-WingetPackage -PackageId 'Python.Python.3.14' -DisplayName 'Python 3.14' -UserScope
        $python = Find-Python314
        if ($null -eq $python) {
            Write-ManualInstallLinks
            throw 'Python 3.14 was not found after installation. Restart Windows if requested, then run setup again.'
        }
    }
    Write-Host ('Ready: Python {0}' -f $python.Version)

    Write-Step 3 'Ensure Google Chrome is installed'
    if ($null -eq $chrome) {
        Install-WingetPackage -PackageId 'Google.Chrome' -DisplayName 'Google Chrome'
        $chrome = Find-Chrome
        if ($null -eq $chrome) {
            Write-ManualInstallLinks
            throw 'Google Chrome was not found after installation. Restart Windows if requested, then run setup again.'
        }
    }
    Write-Host ('Ready: {0}' -f $chrome)

    Write-Step 4 'Prepare the local Python environment'
    $venvDirectory = Join-Path $PSScriptRoot '.venv'
    $venvPythonPath = Join-Path $venvDirectory 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvDirectory)) {
        Invoke-CheckedCommand -Executable $python.Executable -Arguments @('-m', 'venv', $venvDirectory) -FailureMessage 'Could not create .venv'
    }
    $venvPython = Test-Python314 -Path $venvPythonPath -Source 'local .venv'
    if ($null -eq $venvPython) {
        throw 'The existing .venv does not contain a working Python 3.14. Rename or remove only the .venv folder in this app directory, then run setup again.'
    }
    Write-Host ('Ready: {0}' -f $venvPython.Executable)

    Write-Step 5 'Install the pinned Python dependencies'
    $requirementsPath = Join-Path $PSScriptRoot 'requirements.txt'
    if (-not (Test-Path -LiteralPath $requirementsPath -PathType Leaf)) {
        throw 'requirements.txt is missing. Extract the complete app package, then run setup again.'
    }
    $wheelhousePath = Join-Path $PSScriptRoot 'wheelhouse'
    $pipArguments = @('-m', 'pip', '--disable-pip-version-check', 'install', '-r', $requirementsPath)
    if (Test-Path -LiteralPath $wheelhousePath -PathType Container) {
        Write-Host 'Trying the bundled wheelhouse. Its wheels target Python 3.14, 64-bit AMD64 Windows.'
        $offlineArguments = $pipArguments + @('--no-index', '--find-links', $wheelhousePath)
        & $venvPython.Executable @offlineArguments
        $offlineExitCode = $LASTEXITCODE
        if ($offlineExitCode -ne 0) {
            Write-Host ('Bundled wheels could not be installed (exit code {0}); this can happen with a different Python architecture or an incomplete wheelhouse.' -f $offlineExitCode) -ForegroundColor Yellow
            Write-Host ('This environment is {0}-bit {1}. Retrying from the Python package index; internet access is required.' -f $venvPython.Bits, $venvPython.Machine)
            Invoke-CheckedCommand -Executable $venvPython.Executable -Arguments $pipArguments -FailureMessage 'Dependency installation failed. Check the pip messages above and your internet connection'
        }
    } else {
        Write-Host 'Downloading the pinned dependencies from the Python package index.'
        Invoke-CheckedCommand -Executable $venvPython.Executable -Arguments $pipArguments -FailureMessage 'Dependency installation failed. Check the pip messages above and your internet connection'
    }

    Write-Step 6 'Validate the dependencies and course queue'
    Invoke-CheckedCommand -Executable $venvPython.Executable -Arguments @('-B', '-c', "import psutil, websockets, httpx; print('Dependency imports: OK')") -FailureMessage 'The Python dependency import check failed'
    Invoke-CheckedCommand -Executable $venvPython.Executable -Arguments @('-B', (Join-Path $PSScriptRoot 'course_queue.py'), '--validate') -FailureMessage 'The course queue validation failed'

    Write-Step 7 'Configure this PC and save your API key'
    $configurePath = Join-Path $PSScriptRoot 'configure_new_pc.py'
    if (-not (Test-Path -LiteralPath $configurePath -PathType Leaf)) {
        throw 'configure_new_pc.py is missing. Extract the complete app package, then run setup again.'
    }
    Invoke-CheckedCommand -Executable $venvPython.Executable -Arguments @('-B', $configurePath) -FailureMessage 'Configuration was not completed'
    Write-Host ''
    Write-Host 'Setup completed. Start the app with run_queue.cmd.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host ''
    Write-Host ('Setup stopped: {0}' -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
