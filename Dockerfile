# Stage 1: Build the React Frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Python Backend & Execution Environment
FROM python:3.12-slim

# Install Java JDK, Docker CLI, and prerequisites
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-jdk \
    curl \
    gnupg \
    lsb-release \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update && apt-get install -y --no-install-recommends docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Set up secure unprivileged runner user
RUN useradd \
    --create-home \
    --shell /usr/sbin/nologin \
    runner

RUN mkdir -p /app /workspace /runner /tmp/polyworkspace-java-classes \
    && chown -R runner:runner /app /workspace /tmp/polyworkspace-java-classes

WORKDIR /app/backend

# Install Python backend requirements
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application files
COPY backend/ ./

# Copy built React frontend files so FastAPI can serve them at root
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Prevent python output buffering for live console logs
ENV PYTHONUNBUFFERED=1

# Expose the application port
EXPOSE 8001

# Start the FastAPI server using Uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]