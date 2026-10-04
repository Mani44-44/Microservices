FROM python:3.12-slim

WORKDIR /app

COPY services/payment-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/payment-service/app ./app
COPY libs ./libs

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
