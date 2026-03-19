FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

COPY requirements.txt .
RUN uv pip install --system --no-cache -r requirements.txt

COPY . .

# Add entrypoint
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8002  

CMD ["./entrypoint.sh"]