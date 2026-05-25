#!/bin/bash
# =============================================================================
# PayCrypto API 智能客服系统 - 部署脚本
# =============================================================================
# 功能:
#   1. 检查系统环境（Python 版本、pip）
#   2. 创建虚拟环境（可选）
#   3. 安装依赖包
#   4. 生成配置文件模板
#   5. 初始化向量数据库
#   6. 启动服务（开发/生产模式）
#
# 使用方法:
#   ./deploy.sh              # 交互式部署
#   ./deploy.sh --dev       # 开发模式快速部署
#   ./deploy.sh --prod      # 生产模式部署
#   ./deploy.sh --check     # 仅检查环境
# =============================================================================

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 项目根目录
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

# 默认参数
DEPLOY_MODE="interactive"
SKIP_DEPS=false
SKIP_CONFIG=false
USE_VENV=true

# =============================================================================
# 辅助函数
# =============================================================================

print_header() {
    echo ""
    echo -e "${BLUE}==============================================${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}==============================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

# 显示帮助信息
show_help() {
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  --dev       开发模式快速部署（使用热重载）"
    echo "  --prod      生产模式部署（使用多 worker）"
    echo "  --check     仅检查系统环境"
    echo "  --skip-deps 跳过依赖安装"
    echo "  --skip-config  跳过配置生成"
    echo "  --no-venv   不使用虚拟环境"
    echo "  -h, --help  显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0                  # 交互式部署"
    echo "  $0 --dev            # 开发模式"
    echo "  $0 --prod           # 生产模式"
    echo "  $0 --check          # 检查环境"
    exit 0
}

# 解析命令行参数
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --dev)
                DEPLOY_MODE="dev"
                shift
                ;;
            --prod)
                DEPLOY_MODE="prod"
                shift
                ;;
            --check)
                check_env
                exit 0
                ;;
            --skip-deps)
                SKIP_DEPS=true
                shift
                ;;
            --skip-config)
                SKIP_CONFIG=true
                shift
                ;;
            --no-venv)
                USE_VENV=false
                shift
                ;;
            -h|--help)
                show_help
                ;;
            *)
                print_error "未知选项: $1"
                show_help
                ;;
        esac
    done
}

# =============================================================================
# 环境检查
# =============================================================================

check_env() {
    print_header "检查系统环境"

    local all_passed=true

    # 检查 Python 版本
    print_info "检查 Python 版本..."
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
        PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
        PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

        if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 9 ]; then
            print_success "Python 版本: $PYTHON_VERSION (要求 >= 3.9)"
        else
            print_error "Python 版本过低: $PYTHON_VERSION (要求 >= 3.9)"
            all_passed=false
        fi
    else
        print_error "未找到 python3，请先安装 Python 3.9+"
        all_passed=false
    fi

    # 检查 pip
    print_info "检查 pip..."
    if command -v pip3 &> /dev/null; then
        PIP_VERSION=$(pip3 --version 2>&1 | awk '{print $2}')
        print_success "pip 版本: $PIP_VERSION"
    else
        print_error "未找到 pip3，请先安装 pip"
        all_passed=false
    fi

    # 检查端口占用
    print_info "检查端口 8000..."
    if lsof -i:8000 &> /dev/null; then
        print_warning "端口 8000 已被占用"
        lsof -i:8000 | grep LISTEN
    else
        print_success "端口 8000 可用"
    fi

    # 检查磁盘空间
    print_info "检查磁盘空间..."
    DISK_AVAIL=$(df -h "$PROJECT_ROOT" | awk 'NR==2 {print $4}')
    print_success "可用磁盘空间: $DISK_AVAIL"

    # 总结
    echo ""
    if [ "$all_passed" = true ]; then
        print_success "环境检查通过！"
        return 0
    else
        print_error "环境检查失败，请先解决问题"
        return 1
    fi
}

# =============================================================================
# 虚拟环境管理
# =============================================================================

setup_venv() {
    if [ "$USE_VENV" = false ]; then
        print_warning "跳过虚拟环境创建（--no-venv）"
        return 0
    fi

    print_header "设置虚拟环境"

    if [ -d "venv" ]; then
        print_warning "虚拟环境已存在: $PROJECT_ROOT/venv"
        read -p "是否重新创建？(y/N) " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            print_info "删除旧虚拟环境..."
            rm -rf venv
        else
            print_success "使用现有虚拟环境"
            return 0
        fi
    fi

    print_info "创建虚拟环境..."
    python3 -m venv venv
    print_success "虚拟环境创建成功: $PROJECT_ROOT/venv"

    print_info "激活虚拟环境..."
    source venv/bin/activate
    print_success "虚拟环境已激活"

    print_info "升级 pip..."
    pip install --upgrade pip setuptools wheel &> /dev/null
    print_success "pip 升级完成"
}

# =============================================================================
# 依赖安装
# =============================================================================

install_deps() {
    if [ "$SKIP_DEPS" = true ]; then
        print_warning "跳过依赖安装（--skip-deps）"
        return 0
    fi

    print_header "安装依赖包"

    # 确保使用虚拟环境中的 pip
    if [ "$USE_VENV" = true ] && [ -f "venv/bin/activate" ]; then
        source venv/bin/activate
        PIP_CMD="venv/bin/pip"
    else
        PIP_CMD="pip3"
    fi

    print_info "安装 Python 依赖包（这可能需要几分钟）..."
    echo ""

    # 安装依赖
    if $PIP_CMD install -r requirements.txt 2>&1 | tee pip_install.log; then
        print_success "依赖包安装完成"
        rm -f pip_install.log
    else
        print_error "依赖包安装失败，请查看 pip_install.log"
        return 1
    fi

    # 验证关键包
    echo ""
    print_info "验证关键依赖..."
    $PIP_CMD list | grep -E "fastapi|uvicorn|openai|scikit-learn" || true
    print_success "关键依赖验证完成"
}

# =============================================================================
# 配置生成
# =============================================================================

generate_config() {
    if [ "$SKIP_CONFIG" = true ]; then
        print_warning "跳过配置生成（--skip-config）"
        return 0
    fi

    print_header "生成配置文件"

    # 生成 api_config.json
    if [ -f "api_config.json" ]; then
        print_warning "api_config.json 已存在"
        read -p "是否覆盖？(y/N) " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_success "保留现有 api_config.json"
            return 0
        fi
    fi

    print_info "生成 api_config.json 模板..."

    cat > api_config.json << 'EOF'
{
  "providers": {
    "openai": {
      "api_key": "YOUR_OPENAI_API_KEY_HERE",
      "base_url": "https://api.openai.com/v1",
      "model": "gpt-4o-mini",
      "timeout": 30
    },
    "deepseek": {
      "api_key": "YOUR_DEEPSEEK_API_KEY_HERE",
      "base_url": "https://api.deepseek.com/v1",
      "model": "deepseek-chat",
      "timeout": 30
    },
    "siliconflow": {
      "api_key": "YOUR_SILICONFLOW_API_KEY_HERE",
      "base_url": "https://api.siliconflow.cn/v1",
      "model": "Qwen/Qwen2-7B-Instruct",
      "timeout": 30
    }
  },
  "default_provider": "deepseek",
  "memory": {
    "enabled": true,
    "max_history": 20,
    "memory_dir": "./memory_store.json"
  },
  "rag": {
    "top_k": 10,
    "score_threshold": 3.0,
    "use_chroma": false
  }
}
EOF

    print_success "api_config.json 已生成"
    print_warning "请编辑 api_config.json，填入真实的 API Key"

    # 生成 .env 文件
    if [ ! -f ".env" ]; then
        print_info "生成 .env 文件..."
        cat > .env << 'EOF'
# PayCrypto API 客服系统环境变量

# 默认 AI 提供商 (openai / deepseek / siliconflow / custom)
DEFAULT_AI_PROVIDER=deepseek

# 服务端口
PORT=8000

# 日志级别 (DEBUG / INFO / WARNING / ERROR)
LOG_LEVEL=INFO

# 是否使用 Chroma 向量数据库
USE_CHROMA=false

# 内存存储路径
MEMORY_DIR=./memory_store.json
EOF
        print_success ".env 文件已生成"
    else
        print_warning ".env 文件已存在，跳过"
    fi
}

# =============================================================================
# 初始化
# =============================================================================

init_project() {
    print_header "初始化项目"

    # 创建必要目录
    print_info "创建必要目录..."
    mkdir -p static docs_cache obsidian_vault chroma_db mem0_db logs
    print_success "目录创建完成"

    # 检查关键文件
    print_info "检查关键文件..."
    local missing_files=()

    [ ! -f "server.py" ] && missing_files+=("server.py")
    [ ! -f "agent.py" ] && missing_files+=("agent.py")
    [ ! -f "requirements.txt" ] && missing_files+=("requirements.txt")
    [ ! -d "agents" ] && missing_files+=("agents/")

    if [ ${#missing_files[@]} -gt 0 ]; then
        print_error "缺少关键文件: ${missing_files[*]}"
        return 1
    fi
    print_success "关键文件检查通过"

    # 导入知识库文档（可选）
    print_info "是否现在导入知识库文档？(y/N) "
    read -p "" -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_info "请输入知识库文档 URL（多个用空格分隔，直接回车跳过）: "
        read -r doc_urls
        if [ -n "$doc_urls" ]; then
            python3 -c "
from doc_loader import import_doc
urls = '$doc_urls'.split()
for url in urls:
    print(f'导入: {url}')
    import_doc(url)
print('导入完成')
"
        fi
    fi
}

# =============================================================================
# 启动服务
# =============================================================================

start_server() {
    print_header "启动服务"

    # 确定 Python 解释器
    if [ "$USE_VENV" = true ] && [ -f "venv/bin/python" ]; then
        PYTHON_CMD="venv/bin/python"
        UVICORN_CMD="venv/bin/uvicorn"
    else
        PYTHON_CMD="python3"
        UVICORN_CMD="uvicorn"
    fi

    # 检查配置文件
    if [ ! -f "api_config.json" ]; then
        print_error "未找到 api_config.json，请先运行配置生成"
        return 1
    fi

    # 检查 API Key 是否配置
    if grep -q "YOUR_.*_API_KEY_HERE" api_config.json; then
        print_warning "api_config.json 中的 API Key 未配置"
        print_warning "请编辑 api_config.json 填入真实 API Key"
        read -p "是否继续启动（将使用模拟模式）？(y/N) " -n 1 -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi

    # 根据模式启动
    case $DEPLOY_MODE in
        "dev"|"interactive")
            print_info "启动开发模式（热重载）..."
            echo ""
            echo "服务地址: http://localhost:8000"
            echo "前端地址: http://localhost:8000"
            echo "API 文档: http://localhost:8000/docs"
            echo ""
            echo "按 Ctrl+C 停止服务"
            echo ""
            $UVICORN_CMD server:app --host 0.0.0.0 --port 8000 --reload
            ;;
        "prod")
            print_info "启动生产模式（多 worker）..."
            echo ""
            echo "服务地址: http://localhost:8000"
            echo "前端地址: http://localhost:8000"
            echo "API 文档: http://localhost:8000/docs"
            echo ""
            echo "按 Ctrl+C 停止服务"
            echo ""
            $UVICORN_CMD server:app --host 0.0.0.0 --port 8000 --workers 4 --no-access-log
            ;;
    esac
}

# =============================================================================
# 生产环境部署（systemd 服务）
# =============================================================================

setup_systemd() {
    print_header "配置 systemd 服务（生产环境）"

    if [ ! -f "/etc/systemd/system/paycrypto-cs.service" ]; then
        print_info "生成 systemd 服务文件..."

        # 获取当前用户和项目路径
        CURRENT_USER=$(whoami)
        PROJECT_PATH=$(pwd)

        sudo tee /etc/systemd/system/paycrypto-cs.service > /dev/null << EOF
[Unit]
Description=PayCrypto API Customer Service Bot
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$PROJECT_PATH
Environment="PATH=$PROJECT_PATH/venv/bin"
ExecStart=$PROJECT_PATH/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

        print_success "systemd 服务文件已生成"

        print_info "重新加载 systemd..."
        sudo systemctl daemon-reload

        print_info "启用服务（开机自启）..."
        sudo systemctl enable paycrypto-cs.service

        print_success "systemd 服务配置完成"
        echo ""
        print_info "使用以下命令管理服务:"
        echo "  启动: sudo systemctl start paycrypto-cs"
        echo "  停止: sudo systemctl stop paycrypto-cs"
        echo "  重启: sudo systemctl restart paycrypto-cs"
        echo "  状态: sudo systemctl status paycrypto-cs"
        echo "  日志: sudo journalctl -u paycrypto-cs -f"
    else
        print_warning "systemd 服务已存在，跳过"
    fi
}

# =============================================================================
# 主流程
# =============================================================================

main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     PayCrypto API 智能客服系统 - 部署脚本                  ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # 解析参数
    parse_args "$@"

    # 检查环境
    if ! check_env; then
        exit 1
    fi

    # 如果是交互模式，询问部署方式
    if [ "$DEPLOY_MODE" = "interactive" ]; then
        echo ""
        print_info "请选择部署模式:"
        echo "  1) 开发模式（热重载，适合开发调试）"
        echo "  2) 生产模式（多 worker，适合生产环境）"
        echo "  3) 仅安装依赖，不启动服务"
        echo ""
        read -p "请选择 [1-3]: " -n 1 -r
        echo ""
        case $REPLY in
            1) DEPLOY_MODE="dev" ;;
            2) DEPLOY_MODE="prod" ;;
            3) DEPLOY_MODE="install_only" ;;
            *) print_error "无效选择"; exit 1 ;;
        esac
    fi

    # 安装步骤
    if [ "$DEPLOY_MODE" != "install_only" ]; then
        setup_venv
        install_deps
        generate_config
        init_project
    else
        setup_venv
        install_deps
        generate_config
        print_success "依赖安装完成，可以手动启动服务: $0 --dev"
        exit 0
    fi

    # 生产模式额外配置
    if [ "$DEPLOY_MODE" = "prod" ]; then
        read -p "是否配置 systemd 服务（需要 sudo 权限）？(y/N) " -n 1 -r
        echo ""
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            setup_systemd
            print_info "可以使用以下命令启动服务:"
            echo "  sudo systemctl start paycrypto-cs"
            exit 0
        fi
    fi

    # 启动服务
    start_server
}

# 运行主流程
main "$@"
