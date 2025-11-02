from fastapi import FastAPI

app = FastAPI(title="OmniMemory API")

@app.get("/")
def read_root():
    return {"message": "Welcome to the OmniMemory API"}

# Define endpoints for upload, chat, etc. here
