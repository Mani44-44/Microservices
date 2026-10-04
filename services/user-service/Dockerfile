FROM python:3.12-slim

WORKDIR /app

COPY services/user-service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/user-service/app ./app
COPY libs ./libs

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
