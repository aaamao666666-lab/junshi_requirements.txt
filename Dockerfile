FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY junshi_requirements.txt ./junshi_requirements.txt
RUN pip install --no-cache-dir -r junshi_requirements.txt
COPY . .
EXPOSE 10000
CMD ["python", "junshi_master.py"]
