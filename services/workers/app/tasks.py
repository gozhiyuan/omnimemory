from celery import Celery

celery = Celery(__name__)
celery.conf.broker_url = "redis://redis:6379/0"
celery.conf.result_backend = "redis://redis:6379/0"

@celery.task
def preprocess_file(file_id: str):
    print(f"Processing file: {file_id}")
    # Fetch file info from DB
    # Download from storage
    # Run processing steps (OCR, captioning, embedding)
    # Update DB and vector store
    return {"status": "success", "file_id": file_id}
