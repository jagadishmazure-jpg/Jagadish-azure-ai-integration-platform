# One image for every workload of the integration plane (gateways, MCP servers, A2A agents,
# event workers). Container Apps sets the command per workload; see infra/main.bicep.
# Base image pinned by digest (tag kept for readability); Dependabot's docker ecosystem bumps both.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# pip is only needed to install the package; it is not shipped in the runtime image.
RUN pip install . && python -m pip uninstall -y pip && useradd --uid 10001 --no-create-home aiip
USER 10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "aiip.tools.gateway:app", "--host", "0.0.0.0", "--port", "8080"]
