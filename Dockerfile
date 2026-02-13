FROM python:3.12-slim
LABEL maintainer="James Rausch <hello@market2agent.ai>"
LABEL description="Market2Agent — AI Visibility & Trust Platform"

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY app/ app/
COPY sdk/ sdk/
COPY frontend/ frontend/

EXPOSE 8000

ENV M2A_ENV=production
ENV M2A_HOST=0.0.0.0
ENV M2A_PORT=8000

CMD ["uvicorn", "app.main_trust:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
