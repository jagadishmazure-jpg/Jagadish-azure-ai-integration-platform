# One image for every workload of the integration plane (gateways, MCP servers, A2A agents,
# event workers). Container Apps sets the command per workload; see infra/main.bicep.
# Base image pinned by digest (tag kept for readability); Dependabot's docker ecosystem bumps both.
FROM python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# pip is only needed to install the package; it is not shipped in the runtime image.
RUN pip install . && python -m pip uninstall -y pip && useradd --uid 10001 --no-create-home aiip
USER 10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "aiip.tools.gateway:app", "--host", "0.0.0.0", "--port", "8080"]
