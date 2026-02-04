web: uvicorn app.main:app --host 0.0.0.0 --port $PORT
worker: dramatiq app.worker --processes 2 --threads 1 --verbose
