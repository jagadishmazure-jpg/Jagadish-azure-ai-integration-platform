# One image for every workload of the integration plane (gateways, MCP servers, A2A agents,
# event workers). Container Apps sets the command per workload; see infra/main.bicep.
# Base image pinned by digest (tag kept for readability); Dependabot's docker ecosystem bumps both.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# Upgrade Debian packages that have a fix (the Trivy gate fails on fixable HIGH findings),
# then install the app. pip is not shipped in the runtime image.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && pip install . \
    && python -m pip uninstall -y pip \
    && useradd --uid 10001 --no-create-home aiip
USER 10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "aiip.tools.gateway:app", "--host", "0.0.0.0", "--port", "8080"]
