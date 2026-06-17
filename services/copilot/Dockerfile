FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY resource_request_graph.py .
COPY supercoollogo.png .

ENV PORT=8080
EXPOSE 8080

CMD ["python", "app.py"]
