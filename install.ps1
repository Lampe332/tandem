# Windows counterpart of install.sh: fetch tandem if needed, put it on PATH, hook it into Claude Code, install the
# agent skill, then check. Extra arguments go to `tandem install` (for example --accept-inbound).
#
#   irm https://raw.githubusercontent.com/Lampe332/tandem/main/install.ps1 | iex      download and install
#   powershell -ExecutionPolicy Bypass -File install.ps1 [--accept-inbound]         install this checkout in place
#
# Everything runs inside one script block: piped into iex this text runs in the person's own shell, where a
# stray `exit` would close their window and a variable set here would stay behind.
& {
    $ErrorActionPreference = 'Stop'
    $repo, $branch = 'Lampe332/tandem', 'main'

    $python = $false
    try { $python = [bool](& python -c "import sys; print(sys.version_info >= (3, 9))" 2>$null | Select-String True) } catch {}
    if (-not $python) {
        Write-Host 'tandem needs Python 3.9 or newer as `python`. Install it (winget install Python.Python.3.12), open a new shell, run this again.'
        return
    }

    $here = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { $null }
    if (-not $here -or -not (Test-Path (Join-Path $here 'bin\tandem'))) {
        # Piped from the web there is no checkout next to this script: fetch one, then run its installer.
        $target = if ($env:TANDEM_HOME) { $env:TANDEM_HOME } else { Join-Path $HOME '.local\share\tandem' }
        $work = Join-Path ([IO.Path]::GetTempPath()) ('tandem-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Force $work, $target | Out-Null
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12   # Windows PowerShell 5.1 may still offer TLS 1.0
            Invoke-WebRequest "https://github.com/$repo/archive/refs/heads/$branch.zip" -OutFile (Join-Path $work 'tandem.zip') -UseBasicParsing
            Expand-Archive (Join-Path $work 'tandem.zip') -DestinationPath $work
            $source = (Get-ChildItem $work -Directory | Select-Object -First 1).FullName
            # Copied over what is there, never removed first: a hook that fires meanwhile must still find its file.
            Copy-Item (Join-Path $source '*') $target -Recurse -Force
        } finally {
            Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
        }
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $target 'install.ps1') @args
        return
    }

    $shims = Join-Path $here 'win'   # tandem.cmd for cmd/PowerShell, tandem for Git Bash
    $tandem = Join-Path $here 'bin\tandem'

    # One agent on PATH: an entry left by an earlier install elsewhere would win over this one.
    $entries = @([Environment]::GetEnvironmentVariable('Path', 'User') -split ';' | Where-Object { $_ })
    $kept = @($entries | Where-Object { $_ -eq $shims -or -not (Test-Path (Join-Path $_ 'tandem.cmd')) })
    if ($kept -notcontains $shims) { $kept += $shims }
    if (($kept -join ';') -ne ($entries -join ';')) {
        [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
        Write-Host "put $shims on your user PATH (shells and agent sessions started from now on see it)"
    }
    if (($env:Path -split ';') -notcontains $shims) { $env:Path = $shims + ';' + $env:Path }

    python $tandem install @args
    if ($LASTEXITCODE) { return }

    $claude = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
    $codex = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }
    foreach ($engine in $claude, $codex) {
        if (Test-Path $engine) {
            # A copy, not a link: symlinks need Developer Mode or admin rights on Windows. Run this again after updating tandem.
            $skill = Join-Path $engine 'skills\tandem'
            New-Item -ItemType Directory -Force $skill | Out-Null
            Copy-Item (Join-Path $here 'skills\tandem\SKILL.md') (Join-Path $skill 'SKILL.md') -Force
        }
    }

    Write-Host ''
    python $tandem doctor
    Write-Host ''
    Write-Host 'Done. In Claude Code type /tandem, or just ask: "start a Codex helper with tandem".'
} @args
