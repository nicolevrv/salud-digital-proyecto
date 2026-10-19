FROM python:3.11-slim

WORKDIR /app

COPY ml/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY middleware/app/ml_model.py /app/ml_model.py
COPY middleware/app/models /app/models
COPY ml/app.py /app/app.py

EXPOSE 8001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
