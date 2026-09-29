# One image for every workload of the integration plane (gateways, MCP servers, A2A agents,
# event workers). Container Apps sets the command per workload; see infra/main.bicep.
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . && useradd --uid 10001 --no-create-home aiip
USER 10001
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "aiip.tools.gateway:app", "--host", "0.0.0.0", "--port", "8080"]
