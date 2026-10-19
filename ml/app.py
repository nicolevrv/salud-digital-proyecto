from fastapi import FastAPI
from typing import Dict, Any
from ml_model import predecir_3_targets, MODEL_METADATA, FEATURES

app = FastAPI(
    title="BIOT UAO - Servicio ML Biomédico Multi-Objetivo (3 Targets)",
    description="Microservicio de inferencia supervisada para Target_Falla, Target_Mtto_Predictivo y Target_Critico.",
    version="1.0.0",
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "ml-biomedico-3targets",
        "features": FEATURES,
        "targets": list(MODEL_METADATA["targets"].keys()),
    }


@app.get("/metadata")
def metadata():
    return MODEL_METADATA


@app.post("/predict")
def predict(payload: Dict[str, Any]):
    features = payload.get("features_modelo") or payload
    return predecir_3_targets(features)
