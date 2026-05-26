# OneClick SPLAT — Training Backend
# Multi-stage Dockerfile: CUDA 11.8 base, Python 3.10, COLMAP + nerfstudio.
# Build: docker build -t oneclick-splat-backend .
# Run:   docker run --gpus all -p 8000:8000 -v $PWD/jobs:/app/jobs oneclick-splat-backend

FROM nvidia/cuda:11.8.0-cudnn8-devel-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:$PATH

# System deps: Python 3.10, ffmpeg, COLMAP, build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.10 python3.10-venv python3-pip python3.10-dev \
        ffmpeg colmap \
        build-essential git wget ca-certificates \
        libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Create venv and install Python deps
RUN python3.10 -m venv /opt/venv
RUN pip install --upgrade pip wheel setuptools

# PyTorch with CUDA 11.8
RUN pip install torch==2.1.2+cu118 torchvision==0.16.2+cu118 \
        --index-url https://download.pytorch.org/whl/cu118

# nerfstudio
RUN pip install nerfstudio==1.1.4

# FastAPI server deps
RUN pip install \
        fastapi==0.115.0 \
        uvicorn[standard]==0.30.6 \
        sse-starlette==2.1.3 \
        python-multipart==0.0.9 \
        pillow numpy

WORKDIR /app

# Copy backend source
COPY main.py pipeline.py /app/

# Jobs directory (persist via volume mount)
RUN mkdir -p /app/jobs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD wget -q -O - http://127.0.0.1:8000/ || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
