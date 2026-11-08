FROM python:3.12-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" pytest==7.4.3 freezegun coverage

WORKDIR /work
