FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

# 先复制源码和配置文件，再安装（pyproject.toml 需要 src/ 存在才能 pip install -e .）
COPY pyproject.toml constraints.txt ./
COPY src/ src/
COPY scripts/ scripts/
COPY templates/ templates/
COPY config/ config/

# 安装 Python 依赖（使用 constraints.txt 确保可复现构建）
# 生产镜像不安装 dev/transcribe 可选依赖
RUN pip install --no-cache-dir \
    -e ".[transcribe,graph,async,weekly]" \
    -c constraints.txt

# 生产镜像不应在启动时执行完整测试套件。
RUN useradd --create-home --uid 10001 iris \
    && chown -R iris:iris /app
USER iris

# 使用者可通过 docker run ... iris <command> 覆盖命令；默认显示 CLI 帮助。
ENTRYPOINT ["iris"]
CMD ["--help"]
