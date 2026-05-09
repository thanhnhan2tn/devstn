FROM python:3.14-slim
WORKDIR /app
RUN pip install --no-cache-dir fastapi uvicorn httpx redis
COPY bot.py /app/bot.py
CMD ["uvicorn", "bot:app", "--host", "0.0.0.0", "--port", "7700"]