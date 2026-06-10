from fastapi import FastAPI
from pydantic import BaseModel
import numpy as np
import uuid
from datetime import datetime, timezone
from cassandra.cluster import Cluster
from cassandra.auth import PlainTextAuthProvider
from app.classifier import classify_batch
import os
from dotenv import load_dotenv

load_dotenv()

class ClassifyRequest(BaseModel):
    pixels: list[list[int]]

class ClassifyResponse(BaseModel):
    prediction: str
    confidence: float
    scores: dict[str, float]

app = FastAPI()

# ScyllaDB connection
cluster = Cluster([os.getenv("SCYLLA_HOST", "127.0.0.1")])
session = cluster.connect("pixelwise")

INSERT_STMT = session.prepare("""
    INSERT INTO predictions 
    (id, prediction, confidence, model_version, created_at)
    VALUES (?, ?, ?, ?, ?)
""")

@app.get("/health")
def health():
    return {"status": "ok", "model_version": "v1"}

@app.get("/results")
def results():
    rows = session.execute("""
        SELECT id, prediction, confidence, model_version, created_at
        FROM predictions
        WHERE model_version = 'v1'
        ORDER BY created_at DESC
        LIMIT 20
    """)
    return {"results": [
        {
            "id": str(r.id),
            "prediction": r.prediction,
            "confidence": r.confidence,
            "model_version": r.model_version,
            "created_at": r.created_at.isoformat()
        }
        for r in rows
    ]}

@app.post("/classify", response_model=ClassifyResponse)
def classify(req: ClassifyRequest):
    arr = np.array(req.pixels, dtype=np.uint8)[np.newaxis]
    result = classify_batch(arr)[0]
    session.execute(INSERT_STMT, (
        uuid.uuid4(),
        result["prediction"],
        result["confidence"],
        "v1",
        datetime.now(timezone.utc)
    ))
    return result
