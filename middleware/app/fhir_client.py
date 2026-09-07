import os
import requests

HAPI_FHIR_URL = os.getenv("HAPI_FHIR_URL", "http://hapi-fhir:8080/fhir")

def sync_patient_to_fhir(patient_data: dict):
    """Envía o actualiza un recurso Patient en HAPI FHIR"""
    payload = {
        "resourceType": "Patient",
        "identifier": [{
            "system": "http://hospital.org/ni",
            "value": patient_data.get("documento_identidad")
        }],
        "name": [{"text": patient_data.get("nombre")}],
        "active": not patient_data.get("is_deleted", False)
    }
    
    headers = {"Content-Type": "application/fhir+json"}
    
    if patient_data.get("fhir_id"):
        url = f"{HAPI_FHIR_URL}/Patient/{patient_data['fhir_id']}"
        response = requests.put(url, json=payload, headers=headers)
    else:
        url = f"{HAPI_FHIR_URL}/Patient"
        response = requests.post(url, json=payload, headers=headers)
        
    return response.json() if response.status_code in [200, 201] else None

def enviar_observacion_a_fhir(paciente_id_fhir, parametro, loinc_code, valor, unidad, alerta):
    """Envía un recurso Observation en estándar FHIR a HAPI FHIR"""
    payload = {
        "resourceType": "Observation",
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "device",
                        "display": "Device"
                    }
                ]
            }
        ],
        "code": {
            "coding": [
                {
                    "system": "http://loinc.org",
                    "code": loinc_code,
                    "display": parametro
                }
            ],
            "text": parametro
        },
        "subject": {
            "reference": f"Patient/{paciente_id_fhir}"
        },
        "valueQuantity": {
            "value": float(valor),
            "unit": unidad,
            "system": "http://unitsofmeasure.org"
        },
        "note": [{"text": alerta}] if alerta else []
    }

    headers = {"Content-Type": "application/fhir+json"}
    try:
        response = requests.post(f"{HAPI_FHIR_URL}/Observation", json=payload, headers=headers)
        return response.json() if response.status_code in [200, 201] else None
    except Exception as e:
        print(f"Error conectando a HAPI FHIR: {e}")
        return None