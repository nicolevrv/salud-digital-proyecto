import requests

HAPI_FHIR_URL = "http://localhost:8080/fhir"  # O la URL expuesta

def enviar_observacion_a_fhir(paciente_id_fhir, parametro, loinc_code, valor, unidad, alerta):
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
    response = requests.post(f"{HAPI_FHIR_URL}/Observation", json=payload, headers=headers)
    return response.json()