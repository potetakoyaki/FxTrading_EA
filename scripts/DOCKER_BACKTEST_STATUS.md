# MT5 Docker Backtest Setup - Status Report

## Environment (2026-04-06)

| Component | Status | Details |
|-----------|--------|---------|
| Docker Engine | PASS | v29.2.1, daemon running |
| Docker Compose | PASS | v5.0.2 |
| ea31337/ea-tester:latest | PASS | Pulled, 1.1GB |
| ea31337/ea-tester:dev | PASS | Pulled |
| EA-Tester help command | PASS | All commands available |
| EA project files | PASS | AntigravityMTF_EA_Gold.mq5 + Include/ |
| Disk space | PASS | 126GB free |
| Network | PASS | GitHub accessible |

## MT4 Installation via Docker: BLOCKED

### Root Cause
The MT4 installer (`xm4setup.exe` from XM broker) contains anti-debugging protection.
When run under Wine with AutoHotkey (used for headless GUI automation), the installer
detects the AHK process as a "debugger" and shows:

> "A debugger has been found running in your system. Please, unload it from memory
> and restart your program."

Screenshot saved at: `backtest_results/mt4_install_blocked_debugger.png`

### Technical Details
- The ea31337/ea-tester Docker image uses Ubuntu + Wine + Xvfb (virtual display)
- MT4 installation relies on Ansible + winetricks + AutoHotkey for headless GUI automation
- The XM MT4 installer has added anti-debugging checks that detect AHK
- Wine's wineserver may also trigger the detection

### Workaround Options

#### Option 1: Use a Different MT4 Installer (Recommended)
The `install-mt4.yml` Ansible playbook uses XM's installer URL:
```
metatrader_setup_url: https://download.mql5.com/cdn/web/3315/mt4/xm4setup.exe
```
Try a different broker's MT4 installer that may not have this anti-debugging check.
Edit the Ansible playbook or provide a custom verb file.

#### Option 2: Pre-installed MT4 from EA31337/MT-Platforms
The EA-Tester references `EA31337/MT-Platforms` GitHub repo which hosts pre-compiled
platform files. Requires a `GITHUB_API_TOKEN` with access to that repo.
```bash
export GITHUB_API_TOKEN=your_token
docker run --rm -e GITHUB_API_TOKEN=$GITHUB_API_TOKEN ea31337/ea-tester \
  'get_gh_asset EA31337 MT-Platforms 4.0 mt4 /opt'
```

#### Option 3: Manual Installation + Docker Commit
1. Install MT4 on a Windows machine or Wine desktop
2. Copy the MT4 directory into a Docker container
3. Commit the container as a cached image:
```bash
# On a machine with MT4 installed:
docker create --name mt4-setup ea31337/ea-tester true
docker cp /path/to/MetaTrader\ 4/ mt4-setup:/home/ubuntu/.wine/drive_c/Program\ Files/
docker commit mt4-setup ea-tester-mt4-installed
docker rm mt4-setup
```

#### Option 4: GitHub Actions (Best for CI/CD)
The EA-Tester project uses GitHub Actions for its CI. The GitHub Actions runners
have different capabilities that may avoid the anti-debugging issue. Use the
workflow patterns from `.github/workflows/` as reference.

#### Option 5: Use MT5 Instead (Experimental)
MT5 may have a different installer that works better:
```bash
docker run -u root ea31337/ea-tester install_mt 5
```
However, this is also likely to face similar issues.

## Scripts Created

### mt5_docker_backtest.sh
Full-featured backtest runner script supporting:
- `install` - Install MT platform and cache as Docker image
- `backtest` - Run backtest with configurable parameters
- `compile` - Compile EA only
- `shell` - Interactive debugging shell
- `clean` - Remove cached images

### docker_setup_check.sh
Environment diagnostic script that checks all prerequisites.

## Next Steps
1. Try Option 2 (MT-Platforms repo) if you have a GitHub token
2. Try Option 3 (manual installation) on a machine with MT4/MT5
3. Consider GitHub Actions workflow for automated backtesting
