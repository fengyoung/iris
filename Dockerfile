FROM python:3.9-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

# 复制源代码
COPY src/ src/
COPY tests/ tests/
COPY scripts/ scripts/
COPY templates/ templates/
COPY config/ config/
COPY Makefile .

# 默认运行测试
CMD ["make", "test"]
