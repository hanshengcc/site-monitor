FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install playwright browsers
RUN playwright install chromium

# Copy app
COPY backend/ /app/backend/
COPY frontend/dist/ /app/frontend/dist/

# Screenshots dir
RUN mkdir -p /app/screenshots

EXPOSE 8080

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
