# Fallback hosting artifact for the authenticated capture receiver.
# Production HTTPS intake is the Cloudflare Worker in receiver/.
# Do not put secrets in this file.
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY config/ config/

ENV RDX_MARKETING_INTAKE_HOST=0.0.0.0
ENV RDX_MARKETING_INTAKE_PORT=8080
EXPOSE 8080

CMD ["python", "-m", "app.capture_http"]
