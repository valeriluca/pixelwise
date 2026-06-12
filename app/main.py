from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
import numpy as np
import os
from dotenv import load_dotenv
from app.classifier import classify_batch
from app.models import Prediction, SessionLocal

load_dotenv()

def verify_api_key(x_api_key: str = Header(...)):
    if x_api_key != os.getenv("SECRET_API_KEY"):
        raise HTTPException(status_code=401, detail="Invalid API key")

class ClassifyRequest(BaseModel):
    pixels: list[list[int]]

class ClassifyResponse(BaseModel):
    prediction: str
    confidence: float
    scores: dict[str, float]

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok", "model_version": "v1"}

@app.get("/results")
def results():
    db = SessionLocal()
    rows = db.query(Prediction).order_by(Prediction.created_at.desc()).limit(20).all()
    db.close()
    return {"results": [
        {"id": r.id, "prediction": r.prediction, "confidence": r.confidence,
         "model_version": r.model_version, "created_at": r.created_at.isoformat()}
        for r in rows]}

@app.post("/classify", response_model=ClassifyResponse,
          dependencies=[Depends(verify_api_key)])
def classify(req: ClassifyRequest):
    arr = np.array(req.pixels, dtype=np.uint8)[np.newaxis]
    result = classify_batch(arr)[0]
    db = SessionLocal()
    db.add(Prediction(
        prediction=result["prediction"],
        confidence=result["confidence"],
        model_version="v1"))
    db.commit()
    db.close()
    return result
