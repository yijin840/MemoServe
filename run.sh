#!/usr/bin/env bash
# ============================================================
#  MemoServe 启动/停止/重启 管理脚本
#  用法: ./run.sh {start|stop|restart|status|logs}
# ============================================================

set -euo pipefail

# ---- 基础路径 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="$SCRIPT_DIR/.pids"
LOG_DIR="$SCRIPT_DIR/logs"
ENV_FILE="$SCRIPT_DIR/.env"

# ---- 进程定义 ----
# main.py -> uvicorn FastAPI
MAIN_MODULE="main:app"
MAIN_PID="$PID_DIR/main.pid"
MAIN_LOG="$LOG_DIR/main.log"
MAIN_PORT="${PORT:-8000}"

# telegram_bot.py -> python 直接跑
BOT_PID="$PID_DIR/bot.pid"
BOT_LOG="$LOG_DIR/bot.log"

# ---- 颜色 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ============================================================
#  工具函数
# ============================================================

info()  { printf "${GREEN}[INFO]${NC}  %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$*"; }

# 确保目录存在
ensure_dirs() {
    mkdir -p "$PID_DIR" "$LOG_DIR"
}

# 加载 .env（若存在）
load_env() {
    if [[ -f "$ENV_FILE" ]]; then
        set -a
        source "$ENV_FILE"
        set +a
        info "已加载 .env"
    else
        warn ".env 文件不存在: $ENV_FILE"
    fi
}

# 检查进程是否存活
is_running() {
    local pid_file="$1"
    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        else
            # pid 文件存在但进程已死，清理
            rm -f "$pid_file"
            return 1
        fi
    fi
    return 1
}

# 优雅停止进程
graceful_stop() {
    local pid_file="$1"
    local name="$2"
    local timeout="${3:-15}"  # 默认等待 15 秒

    if ! is_running "$pid_file"; then
        info "$name 未运行"
        return 0
    fi

    local pid
    pid=$(cat "$pid_file")
    info "正在停止 $name (PID: $pid)..."

    # 先发 SIGTERM
    kill -TERM "$pid" 2>/dev/null || true

    local waited=0
    while [[ $waited -lt $timeout ]]; do
        if ! kill -0 "$pid" 2>/dev/null; then
            break
        fi
        sleep 1
        waited=$((waited + 1))
    done

    if kill -0 "$pid" 2>/dev/null; then
        warn "$name 未在 ${timeout}s 内退出，发送 SIGKILL"
        kill -KILL "$pid" 2>/dev/null || true
        sleep 2
    fi

    if ! kill -0 "$pid" 2>/dev/null; then
        info "$name 已停止 (PID: $pid)"
    else
        error "$name 无法停止 (PID: $pid)"
        return 1
    fi

    rm -f "$pid_file"
}

# ============================================================
#  start
# ============================================================
cmd_start() {
    ensure_dirs
    load_env

    # ---------- 启动 main.py (FastAPI) ----------
    if is_running "$MAIN_PID"; then
        local pid
        pid=$(cat "$MAIN_PID")
        warn "main 已在运行 (PID: $pid)，跳过。用 restart 或 stop 后再 start。"
    else
        info "启动 main (FastAPI on :$MAIN_PORT) ..."
        nohup uvicorn \
            --host 0.0.0.0 \
            --port "$MAIN_PORT" \
            --workers 1 \
            --log-level info \
            "$MAIN_MODULE" \
            >> "$MAIN_LOG" 2>&1 &
        echo $! > "$MAIN_PID"
        info "main PID: $(cat "$MAIN_PID")  日志: $MAIN_LOG"

        # 等待启动
        sleep 2
        if is_running "$MAIN_PID"; then
            info "main 启动成功"
        else
            error "main 启动失败，请查看日志: $MAIN_LOG"
            rm -f "$MAIN_PID"
        fi
    fi

    # ---------- 启动 telegram_bot.py ----------
    if is_running "$BOT_PID"; then
        local pid
        pid=$(cat "$BOT_PID")
        warn "bot 已在运行 (PID: $pid)，跳过。"
    else
        info "启动 telegram_bot ..."
        nohup python3 "$SCRIPT_DIR/telegram_bot.py" \
            >> "$BOT_LOG" 2>&1 &
        echo $! > "$BOT_PID"
        info "bot PID: $(cat "$BOT_PID")  日志: $BOT_LOG"

        sleep 2
        if is_running "$BOT_PID"; then
            info "bot 启动成功"
        else
            error "bot 启动失败，请查看日志: $BOT_LOG"
            rm -f "$BOT_PID"
        fi
    fi
}

# ============================================================
#  stop
# ============================================================
cmd_stop() {
    info "停止所有服务..."
    graceful_stop "$MAIN_PID" "main" 15
    graceful_stop "$BOT_PID"  "bot"  15
    info "所有服务已停止"
}

# ============================================================
#  restart
# ============================================================
cmd_restart() {
    info "重启所有服务..."
    cmd_stop
    sleep 2
    cmd_start
}

# ============================================================
#  status
# ============================================================
cmd_status() {
    echo ""
    echo "===== MemoServe 服务状态 ====="

    if is_running "$MAIN_PID"; then
        local pid
        pid=$(cat "$MAIN_PID")
        printf "  ${GREEN}●${NC} main  (PID: %s, 端口: %s)\n" "$pid" "$MAIN_PORT"
    else
        printf "  ${RED}○${NC} main  (未运行)\n"
    fi

    if is_running "$BOT_PID"; then
        local pid
        pid=$(cat "$BOT_PID")
        printf "  ${GREEN}●${NC} bot   (PID: %s)\n" "$pid"
    else
        printf "  ${RED}○${NC} bot   (未运行)\n"
    fi

    echo ""
    echo "日志目录: $LOG_DIR"
    echo "PID 目录: $PID_DIR"
    echo ""
}

# ============================================================
#  logs  (tail -f)
# ============================================================
cmd_logs() {
    local target="${1:-all}"

    case "$target" in
        main) tail -f "$MAIN_LOG" ;;
        bot)  tail -f "$BOT_LOG"  ;;
        all)
            if command -v multitail &>/dev/null; then
                multitail "$MAIN_LOG" "$BOT_LOG"
            else
                # fallback: tail 两个文件（交替输出）
                tail -f "$MAIN_LOG" "$BOT_LOG"
            fi
            ;;
        *)
            error "用法: $0 logs {main|bot|all}"
            exit 1
            ;;
    esac
}

# ============================================================
#  main
# ============================================================
case "${1:-}" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    restart) cmd_restart ;;
    status)  cmd_status  ;;
    logs)    cmd_logs "${2:-all}" ;;
    *)
        echo ""
        echo "用法: $0 {start|stop|restart|status|logs [main|bot|all]}"
        echo ""
        echo "  start   - 启动所有服务 (main + bot)"
        echo "  stop    - 优雅停止所有服务"
        echo "  restart - 重启所有服务"
        echo "  status  - 查看运行状态"
        echo "  logs    - 实时查看日志 (默认 all，可选 main / bot)"
        echo ""
        exit 1
        ;;
esac
