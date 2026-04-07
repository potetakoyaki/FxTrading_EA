#!/usr/bin/env bash
# mt5_docker_backtest.sh - MT Docker-based backtesting for AntigravityMTF EA Gold
#
# Uses ea31337/ea-tester Docker image to run headless MT4/MT5 backtests.
#
# Architecture:
#   1. First run: installs MT platform inside Docker (takes 5-10 min), saves as local image
#   2. Subsequent runs: uses cached image, skips installation (fast)
#   3. Copies EA files, compiles, downloads data, runs backtest, extracts results
#
# Prerequisites:
#   - Docker installed and running
#   - Internet access (for MT download and backtest data fetch)
#   - ~5GB free disk space (for Docker images + MT platform + Wine)
#
# Usage:
#   ./mt5_docker_backtest.sh [command] [options]
#
# Commands:
#   backtest   Run a backtest (default)
#   install    Install MT platform only (creates cached image)
#   compile    Compile EA only (syntax check)
#   shell      Open interactive shell in container
#   clean      Remove cached Docker images
#
# Examples:
#   # First time: install MT4 (takes ~10 min, cached for future runs)
#   ./mt5_docker_backtest.sh install
#
#   # Quick backtest with defaults (XAUUSD, 2024, M15)
#   ./mt5_docker_backtest.sh backtest
#
#   # Backtest specific year range with spread
#   ./mt5_docker_backtest.sh backtest -y 2023-2024 -S 30
#
#   # Backtest with custom EA file and SET file
#   ./mt5_docker_backtest.sh backtest -e /path/to/MyEA.mq5 -f /path/to/params.set
#
#   # Optimization mode
#   ./mt5_docker_backtest.sh backtest -o -y 2023
#
#   # Dry run (show what would be executed)
#   ./mt5_docker_backtest.sh backtest -_ -v

set -euo pipefail

# =============================================================================
# Configuration Defaults
# =============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Docker
DOCKER_IMAGE="ea31337/ea-tester"
DOCKER_TAG="latest"
CACHED_IMAGE_MT4="ea-tester-mt4-installed"
CACHED_IMAGE_MT5="ea-tester-mt5-installed"
CONTAINER_NAME="ea-backtest-$$"

# MT Platform
MT_VERSION="4"  # 4 or 5

# Backtest Parameters
EA_FILE="${PROJECT_DIR}/AntigravityMTF_EA_Gold.mq5"
EA_NAME="AntigravityMTF_EA_Gold"
SYMBOL="XAUUSD"
TIMEFRAME="M15"
YEAR="2024"
MONTHS="1-12"
SPREAD="30"
DEPOSIT="10000"
CURRENCY="USD"
LEVERAGE="100"
BT_MODEL="0"  # 0=Every tick, 1=Control points, 2=Open prices
BT_SOURCE="DS"  # DS=Dukascopy, MQ=MetaQuotes

# Output
RESULTS_DIR="${PROJECT_DIR}/backtest_results"
VERBOSE=false
FORMAT_JSON=false
FORMAT_TEXT=true
OPTIMIZATION=false
SET_FILE=""
TRACE=false
DRY_RUN=false

# EA Parameters (comma-separated key=value pairs to override in .set file)
EA_PARAMS=""

# =============================================================================
# Functions
# =============================================================================

usage() {
    cat <<'USAGE'
MT Docker Backtest Runner for AntigravityMTF EA Gold

Usage: mt5_docker_backtest.sh [command] [options]

Commands:
  backtest   Run a backtest (default if no command specified)
  install    Install MT platform only (creates cached image for fast re-use)
  compile    Compile EA only (syntax check)
  shell      Open interactive shell in container
  clean      Remove cached Docker images

Options:
  -e FILE      EA source file (.mq5/.mq4/.ex5/.ex4) [default: AntigravityMTF_EA_Gold.mq5]
  -p SYMBOL    Symbol pair [default: XAUUSD]
  -T PERIOD    Timeframe (M1/M5/M15/M30/H1/H4/D1) [default: M15]
  -y YEAR      Year or range (e.g., 2024, 2023-2024) [default: 2024]
  -m MONTHS    Month range (e.g., 1-12, 1-6) [default: 1-12]
  -S SPREAD    Spread in points [default: 30]
  -d AMOUNT    Deposit amount [default: 10000]
  -c CURRENCY  Base currency [default: USD]
  -f FILE      SET file with EA parameters
  -P PARAMS    EA params override (e.g., "RiskPercent=0.5,SL_ATR_Multi=1.5")
  -b SOURCE    Backtest data source (DS/MQ) [default: DS]
  -M VERSION   MT version (4/5) [default: 4]
  -o           Enable optimization mode
  -O DIR       Output directory [default: ./backtest_results]
  -j           Output results as JSON
  -v           Verbose mode
  -x           Trace/debug mode
  -_           Dry run (show what would be executed)
  -h           Show this help

Environment Variables:
  BT_LEVERAGE    Account leverage (default: 100)
  BT_TESTMODEL   Backtest model: 0=EveryTick, 1=ControlPoints, 2=OpenPrices (default: 0)
  DOCKER_IMAGE   Docker image (default: ea31337/ea-tester)
  DOCKER_TAG     Docker tag (default: latest)
  GITHUB_API_TOKEN  GitHub token for private data repos (optional)

USAGE
    exit 0
}

log_info()  { echo "[INFO]  $(date '+%H:%M:%S') $*"; }
log_warn()  { echo "[WARN]  $(date '+%H:%M:%S') $*" >&2; }
log_error() { echo "[ERROR] $(date '+%H:%M:%S') $*" >&2; }
log_debug() { $VERBOSE && echo "[DEBUG] $(date '+%H:%M:%S') $*" || true; }

get_cached_image() {
    local mt_ver="${1:-$MT_VERSION}"
    if [ "$mt_ver" = "5" ]; then
        echo "$CACHED_IMAGE_MT5"
    else
        echo "$CACHED_IMAGE_MT4"
    fi
}

check_docker() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed. Install Docker first:"
        log_error "  curl -fsSL https://get.docker.com | sh"
        log_error "  sudo usermod -aG docker \$USER"
        exit 1
    fi
    if ! docker info &>/dev/null; then
        log_error "Docker daemon is not running. Start it with:"
        log_error "  sudo systemctl start docker"
        exit 1
    fi
    log_info "Docker is available: $(docker --version)"
}

pull_base_image() {
    local full_image="${DOCKER_IMAGE}:${DOCKER_TAG}"
    if docker image inspect "$full_image" &>/dev/null; then
        log_info "Base image $full_image already available."
    else
        log_info "Pulling base image $full_image ..."
        docker pull "$full_image"
    fi
}

has_cached_image() {
    local cached
    cached=$(get_cached_image)
    docker image inspect "$cached" &>/dev/null
}

prepare_results_dir() {
    mkdir -p "$RESULTS_DIR"
    log_info "Results will be saved to: $RESULTS_DIR"
}

# =============================================================================
# Command: install - Install MT platform and cache as Docker image
# =============================================================================
cmd_install() {
    local cached
    cached=$(get_cached_image)

    if has_cached_image; then
        log_info "Cached image '$cached' already exists."
        log_info "Use '$0 clean' to remove it and reinstall."
        return 0
    fi

    local full_image="${DOCKER_IMAGE}:${DOCKER_TAG}"
    pull_base_image

    log_info "Installing MT${MT_VERSION} platform (this takes 5-10 minutes on first run)..."
    log_info "The result will be cached as Docker image '$cached' for future use."

    local install_container="${CONTAINER_NAME}-install"

    # Run MT installation as root (needed for Ansible/winetricks)
    docker run \
        --name "$install_container" \
        -u root \
        "$full_image" \
        install_mt "$MT_VERSION" 2>&1 | tee "${RESULTS_DIR}/install.log"

    local exit_code=${PIPESTATUS[0]}

    if [ $exit_code -eq 0 ]; then
        log_info "Installation successful. Saving as cached image..."
        docker commit "$install_container" "$cached" >/dev/null
        log_info "Cached image saved as: $cached"
    else
        log_warn "Installation exited with code $exit_code."
        log_warn "Saving image anyway (MT install sometimes reports non-zero on success)..."
        docker commit "$install_container" "$cached" >/dev/null
        log_info "Cached image saved as: $cached (verify with 'shell' command)"
    fi

    docker rm -f "$install_container" >/dev/null 2>&1 || true

    # Verify installation
    log_info "Verifying MT installation..."
    docker run --rm --entrypoint /bin/bash "$cached" -c '
        cd /opt/scripts
        source .vars.inc.sh
        echo "MT4_DIR=$TERMINAL4_DIR"
        echo "MT4_EXE=$TERMINAL4_EXE"
        echo "MT5_DIR=$TERMINAL5_DIR"
        echo "MT5_EXE=$TERMINAL5_EXE"
        if [ -n "$TERMINAL4_EXE" ] || [ -n "$TERMINAL5_EXE" ]; then
            echo "VERIFIED: MT platform found."
        else
            echo "WARNING: MT platform not found in expected paths."
        fi
    ' 2>&1

    return 0
}

# =============================================================================
# Command: backtest - Run a full backtest
# =============================================================================
cmd_backtest() {
    local cached
    cached=$(get_cached_image)

    # Check if we have a cached image with MT installed
    if ! has_cached_image; then
        log_warn "No cached MT${MT_VERSION} installation found."
        log_info "Installing MT${MT_VERSION} first (this is a one-time setup)..."
        prepare_results_dir
        cmd_install
    fi

    local ea_basename
    ea_basename="$(basename "$EA_FILE")"

    log_info "=== Backtest Configuration ==="
    log_info "EA:         $EA_NAME ($EA_FILE)"
    log_info "Symbol:     $SYMBOL"
    log_info "Timeframe:  $TIMEFRAME"
    log_info "Period:     $YEAR (months: $MONTHS)"
    log_info "Spread:     $SPREAD points"
    log_info "Deposit:    $DEPOSIT $CURRENCY"
    log_info "Leverage:   $LEVERAGE"
    log_info "BT Model:   $BT_MODEL (0=Tick,1=CP,2=Open)"
    log_info "Data Src:   $BT_SOURCE"
    log_info "Results:    $RESULTS_DIR"
    log_info "=============================="

    # Build EA-Tester arguments
    local bt_args=()
    bt_args+=("-e" "$EA_NAME")
    bt_args+=("-p" "$SYMBOL")
    bt_args+=("-T" "$TIMEFRAME")
    bt_args+=("-y" "$YEAR")
    bt_args+=("-m" "$MONTHS")
    bt_args+=("-S" "$SPREAD")
    bt_args+=("-d" "$DEPOSIT")
    bt_args+=("-c" "$CURRENCY")
    bt_args+=("-b" "$BT_SOURCE")
    [ -n "$SET_FILE" ] && bt_args+=("-f" "/opt/ea-files/$(basename "$SET_FILE")")
    [ -n "$EA_PARAMS" ] && bt_args+=("-P" "$EA_PARAMS")
    $FORMAT_TEXT && bt_args+=("-t")
    $FORMAT_JSON && bt_args+=("-j")
    $VERBOSE && bt_args+=("-v")
    $OPTIMIZATION && bt_args+=("-o")
    $TRACE && bt_args+=("-x")

    # Build volume mounts
    local vol_args=()
    vol_args+=("-v" "${PROJECT_DIR}:/opt/ea-files:ro")
    [ -d "${PROJECT_DIR}/Include" ] && vol_args+=("-v" "${PROJECT_DIR}/Include:/opt/ea-includes:ro")
    vol_args+=("-v" "${RESULTS_DIR}:/opt/results:rw")
    if [ -n "$SET_FILE" ] && [ -f "$SET_FILE" ]; then
        vol_args+=("-v" "$(realpath "$SET_FILE"):/opt/ea-files/$(basename "$SET_FILE"):ro")
    fi

    # Build startup script to copy EA files into the right locations
    local run_on_start
    run_on_start=$(cat <<'INNER_EOF'
echo "INFO: Copying EA files to platform directories..."
if [ -n "$EXPERTS_DIR" ] && [ -d "$EXPERTS_DIR" ]; then
    find /opt/ea-files -maxdepth 1 \( -name "*.mq4" -o -name "*.mq5" -o -name "*.ex4" -o -name "*.ex5" \) -exec cp -v {} "$EXPERTS_DIR/" \;
    MQL_BASE="$(dirname "$EXPERTS_DIR")"
    if [ -d /opt/ea-includes ]; then
        mkdir -p "$MQL_BASE/Include" 2>/dev/null || true
        cp -rv /opt/ea-includes/* "$MQL_BASE/Include/" 2>/dev/null || true
    fi
    echo "INFO: EA files in $EXPERTS_DIR:"
    ls -la "$EXPERTS_DIR/"
fi
INNER_EOF
    )

    if $DRY_RUN; then
        log_info "[DRY RUN] Would execute:"
        echo "docker run --rm --name $CONTAINER_NAME \\"
        printf "  %s \\\\\n" "${vol_args[@]}"
        echo "  -e MT_VER=$MT_VERSION \\"
        echo "  -e BT_LEVERAGE=$LEVERAGE \\"
        echo "  -e BT_TESTMODEL=$BT_MODEL \\"
        echo "  -e RUN_ON_START=<startup_script> \\"
        echo "  $cached \\"
        printf "  run_backtest %s\n" "${bt_args[*]}"
        return 0
    fi

    log_info "Starting backtest..."
    local start_time
    start_time=$(date +%s)

    # Run the backtest
    docker run --rm \
        --name "$CONTAINER_NAME" \
        "${vol_args[@]}" \
        -e "MT_VER=$MT_VERSION" \
        -e "BT_LEVERAGE=$LEVERAGE" \
        -e "BT_TESTMODEL=$BT_MODEL" \
        -e "RUN_ON_START=$run_on_start" \
        "$cached" \
        run_backtest "${bt_args[@]}" \
        2>&1 | tee "${RESULTS_DIR}/backtest_console.log"

    local exit_code=${PIPESTATUS[0]}
    local end_time
    end_time=$(date +%s)
    local duration=$((end_time - start_time))

    log_info "Backtest completed in ${duration}s (exit code: $exit_code)"

    if [ -d "$RESULTS_DIR" ]; then
        log_info "Result files:"
        ls -la "$RESULTS_DIR/" 2>/dev/null || true
    fi

    return $exit_code
}

# =============================================================================
# Command: compile - Compile EA only
# =============================================================================
cmd_compile() {
    local cached
    cached=$(get_cached_image)

    if ! has_cached_image; then
        log_error "No cached MT installation. Run '$0 install' first."
        exit 1
    fi

    local ea_basename
    ea_basename="$(basename "$EA_FILE")"

    log_info "Compiling EA: $ea_basename"

    docker run --rm \
        --name "$CONTAINER_NAME" \
        -v "${PROJECT_DIR}:/opt/ea-files:ro" \
        -v "${RESULTS_DIR}:/opt/results:rw" \
        -e "RUN_ON_START=cp -v /opt/ea-files/$ea_basename \$EXPERTS_DIR/ 2>/dev/null || true" \
        "$cached" \
        compile "\$EXPERTS_DIR/$ea_basename" "/opt/results/compile.log" \
        2>&1 | tee "${RESULTS_DIR}/compile.log"

    local exit_code=${PIPESTATUS[0]}
    if [ $exit_code -eq 0 ]; then
        log_info "Compilation successful."
    else
        log_error "Compilation failed. See ${RESULTS_DIR}/compile.log"
    fi
    return $exit_code
}

# =============================================================================
# Command: shell - Open interactive shell
# =============================================================================
cmd_shell() {
    local image
    if has_cached_image; then
        image=$(get_cached_image)
        log_info "Starting shell with cached MT image: $image"
    else
        image="${DOCKER_IMAGE}:${DOCKER_TAG}"
        log_info "Starting shell with base image: $image (MT not installed)"
    fi

    docker run -it --rm \
        --name "$CONTAINER_NAME" \
        -v "${PROJECT_DIR}:/opt/ea-files:rw" \
        -v "${RESULTS_DIR}:/opt/results:rw" \
        --entrypoint /bin/bash \
        "$image"
}

# =============================================================================
# Command: clean - Remove cached images
# =============================================================================
cmd_clean() {
    log_info "Removing cached Docker images..."
    docker rmi "$CACHED_IMAGE_MT4" 2>/dev/null && log_info "Removed $CACHED_IMAGE_MT4" || log_info "$CACHED_IMAGE_MT4 not found."
    docker rmi "$CACHED_IMAGE_MT5" 2>/dev/null && log_info "Removed $CACHED_IMAGE_MT5" || log_info "$CACHED_IMAGE_MT5 not found."
    log_info "Cleanup complete."
}

# =============================================================================
# Cleanup handler
# =============================================================================
cleanup() {
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
    docker rm -f "${CONTAINER_NAME}-install" 2>/dev/null || true
}

# =============================================================================
# Parse Arguments
# =============================================================================

# Extract command (first non-option argument)
COMMAND="backtest"
if [ $# -gt 0 ] && [[ ! "$1" =~ ^- ]]; then
    COMMAND="$1"
    shift
fi

while getopts "e:p:T:y:m:S:d:c:f:P:b:M:oO:jvxh_" opt; do
    case $opt in
        e) EA_FILE="$OPTARG"
           EA_NAME="$(basename "${OPTARG%.*}")"
           ;;
        p) SYMBOL="$OPTARG" ;;
        T) TIMEFRAME="$OPTARG" ;;
        y) YEAR="$OPTARG" ;;
        m) MONTHS="$OPTARG" ;;
        S) SPREAD="$OPTARG" ;;
        d) DEPOSIT="$OPTARG" ;;
        c) CURRENCY="$OPTARG" ;;
        f) SET_FILE="$OPTARG" ;;
        P) EA_PARAMS="$OPTARG" ;;
        b) BT_SOURCE="$OPTARG" ;;
        M) MT_VERSION="$OPTARG" ;;
        o) OPTIMIZATION=true ;;
        O) RESULTS_DIR="$OPTARG" ;;
        j) FORMAT_JSON=true ;;
        v) VERBOSE=true ;;
        x) TRACE=true ;;
        _) DRY_RUN=true ;;
        h) usage ;;
        *) usage ;;
    esac
done

# Apply environment variable overrides
LEVERAGE="${BT_LEVERAGE:-$LEVERAGE}"
BT_MODEL="${BT_TESTMODEL:-$BT_MODEL}"

# =============================================================================
# Main
# =============================================================================

trap cleanup EXIT

log_info "MT Docker Backtest Runner for AntigravityMTF EA Gold"
log_info "======================================================"

# Check Docker for all commands
check_docker

# Prepare results directory
prepare_results_dir

case "$COMMAND" in
    install)
        pull_base_image
        cmd_install
        ;;
    backtest)
        # Validate EA file
        if [ ! -f "$EA_FILE" ]; then
            log_error "EA file not found: $EA_FILE"
            log_error "Specify with -e option, e.g.: $0 backtest -e /path/to/EA.mq5"
            exit 1
        fi
        pull_base_image
        cmd_backtest
        ;;
    compile)
        if [ ! -f "$EA_FILE" ]; then
            log_error "EA file not found: $EA_FILE"
            exit 1
        fi
        cmd_compile
        ;;
    shell)
        cmd_shell
        ;;
    clean)
        cmd_clean
        ;;
    *)
        log_error "Unknown command: $COMMAND"
        usage
        ;;
esac

exit $?
