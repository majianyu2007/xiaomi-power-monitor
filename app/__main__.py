"""FastAPI 入口"""
from app.main import app
import uvicorn
import os

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))