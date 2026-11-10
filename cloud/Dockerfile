FROM python:3.11-slim
WORKDIR /srv
COPY cloud/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY cloud/ /srv/cloud/
CMD ["uvicorn", "cloud.server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
