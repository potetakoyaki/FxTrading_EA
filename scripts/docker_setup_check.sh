#!/usr/bin/env bash
# docker_setup_check.sh - Verify Docker environment for MT5 backtesting
#
# Checks all prerequisites and provides actionable fix instructions.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "  ${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "  ${RED}[FAIL]${NC} $*"; }
warn() { echo -e "  ${YELLOW}[WARN]${NC} $*"; }
info() { echo -e "  [INFO] $*"; }

ERRORS=0

echo "============================================"
echo "  MT5 Docker Backtest Environment Check"
echo "============================================"
echo ""

# 1. Docker installation
echo "1. Docker Installation"
if command -v docker &>/dev/null; then
    pass "Docker installed: $(docker --version)"
else
    fail "Docker not installed"
    info "Install: curl -fsSL https://get.docker.com | sh"
    info "Then:    sudo usermod -aG docker \$USER && newgrp docker"
    ((ERRORS++))
fi

# 2. Docker daemon
echo "2. Docker Daemon"
if docker info &>/dev/null 2>&1; then
    pass "Docker daemon is running"
    info "Storage: $(docker info 2>/dev/null | grep 'Storage Driver' | head -1 | xargs)"
    info "OS: $(docker info 2>/dev/null | grep 'Operating System' | head -1 | xargs)"
else
    fail "Docker daemon is not running"
    info "Start: sudo systemctl start docker"
    info "Enable: sudo systemctl enable docker"
    ((ERRORS++))
fi

# 3. Docker permissions
echo "3. Docker Permissions"
if docker run --rm hello-world &>/dev/null 2>&1; then
    pass "Can run Docker containers without sudo"
else
    warn "May need sudo for Docker commands"
    info "Fix: sudo usermod -aG docker \$USER && newgrp docker"
fi

# 4. Docker Compose
echo "4. Docker Compose"
if docker compose version &>/dev/null 2>&1; then
    pass "Docker Compose: $(docker compose version 2>/dev/null | head -1)"
elif command -v docker-compose &>/dev/null; then
    pass "Docker Compose (legacy): $(docker-compose --version 2>/dev/null)"
else
    warn "Docker Compose not found (optional, not required for basic usage)"
fi

# 5. EA-Tester image
echo "5. EA-Tester Docker Image"
if docker image inspect ea31337/ea-tester:latest &>/dev/null 2>&1; then
    local_size=$(docker image inspect ea31337/ea-tester:latest --format='{{.Size}}' 2>/dev/null)
    pass "ea31337/ea-tester:latest available ($(numfmt --to=iec "$local_size" 2>/dev/null || echo "${local_size} bytes"))"
else
    warn "ea31337/ea-tester:latest not pulled yet"
    info "Pull: docker pull ea31337/ea-tester:latest"
fi

if docker image inspect ea31337/ea-tester:dev &>/dev/null 2>&1; then
    pass "ea31337/ea-tester:dev available"
else
    info "ea31337/ea-tester:dev not pulled (optional)"
fi

# 6. EA files
echo "6. EA Project Files"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

EA_MAIN="${PROJECT_DIR}/AntigravityMTF_EA_Gold.mq5"
if [ -f "$EA_MAIN" ]; then
    pass "Main EA file: $EA_MAIN"
else
    fail "Main EA file not found at: $EA_MAIN"
    ((ERRORS++))
fi

EA_V17="${PROJECT_DIR}/AntigravityMTF_EA_Gold_v17.mq5"
if [ -f "$EA_V17" ]; then
    pass "EA v17 file: $EA_V17"
else
    info "EA v17 file not found (optional): $EA_V17"
fi

INCLUDE_DIR="${PROJECT_DIR}/Include"
if [ -d "$INCLUDE_DIR" ]; then
    include_count=$(find "$INCLUDE_DIR" -name "*.mqh" | wc -l)
    pass "Include directory: $INCLUDE_DIR ($include_count .mqh files)"
else
    warn "Include directory not found: $INCLUDE_DIR"
fi

# 7. Disk space
echo "7. Disk Space"
avail_kb=$(df -k /var/lib/docker 2>/dev/null | tail -1 | awk '{print $4}')
if [ -n "$avail_kb" ] && [ "$avail_kb" -gt 5242880 ]; then
    pass "Sufficient disk space: $(numfmt --to=iec --from-unit=1024 "$avail_kb" 2>/dev/null || echo "${avail_kb}K") available"
else
    warn "Less than 5GB free on Docker partition"
    info "MT platform installation requires ~2-3GB"
fi

# 8. Network (for data download)
echo "8. Network Connectivity"
if timeout 5 curl -s -o /dev/null -w "%{http_code}" https://github.com 2>/dev/null | grep -q "200\|301\|302"; then
    pass "GitHub accessible (needed for backtest data)"
else
    warn "Cannot reach GitHub (backtest data download may fail)"
fi

# 9. EA-Tester reference
echo "9. EA-Tester Reference"
EA_TESTER_DIR="${PROJECT_DIR}/reference/EA-Tester"
if [ -d "$EA_TESTER_DIR" ]; then
    pass "EA-Tester reference: $EA_TESTER_DIR"
else
    warn "EA-Tester reference not found at: $EA_TESTER_DIR"
    info "Clone: git clone https://github.com/EA31337/EA-Tester.git $EA_TESTER_DIR"
fi

# 10. Quick smoke test
echo "10. Smoke Test"
if docker image inspect ea31337/ea-tester:latest &>/dev/null 2>&1; then
    if docker run --rm ea31337/ea-tester help &>/dev/null 2>&1; then
        pass "ea31337/ea-tester 'help' command works"
    else
        warn "ea31337/ea-tester 'help' command failed"
    fi
else
    info "Skipping smoke test (image not pulled)"
fi

echo ""
echo "============================================"
if [ "$ERRORS" -gt 0 ]; then
    echo -e "  ${RED}$ERRORS critical issue(s) found.${NC}"
    echo "  Fix the FAIL items above before running backtests."
else
    echo -e "  ${GREEN}All critical checks passed.${NC}"
    echo "  Ready for Docker-based backtesting."
fi
echo "============================================"
echo ""
echo "Quick Start:"
echo "  ./mt5_docker_backtest.sh -_ -v    # Dry run to see config"
echo "  ./mt5_docker_backtest.sh -v        # Run actual backtest"
echo ""

exit $ERRORS
