FROM python:3.12-slim
WORKDIR /srv
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
EXPOSE 8080
CMD ["uvicorn", "famulus.main:app", "--host", "0.0.0.0", "--port", "8080"]
