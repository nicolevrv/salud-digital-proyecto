import os
import requests

HAPI_FHIR_URL = os.getenv("HAPI_FHIR_URL", "http://hapi-fhir:8080/fhir")
HEADERS = {"Content-Type": "application/fhir+json"}

def sync_patient_to_fhir(patient_data: dict):
    """Envía o actualiza un recurso Patient en HAPI FHIR"""
    payload = {
        "resourceType": "Patient",
        "identifier": [{
            "system": "http://hospital.org/ni",
            "value": str(patient_data.get("documento_identidad"))
        }],
        "name": [{"text": patient_data.get("nombre")}],
        "active": not patient_data.get("is_deleted", False)
    }
    
    try:
        if patient_data.get("fhir_id"):
            url = f"{HAPI_FHIR_URL}/Patient/{patient_data['fhir_id']}"
            response = requests.put(url, json=payload, headers=HEADERS)
        else:
            url = f"{HAPI_FHIR_URL}/Patient"
            response = requests.post(url, json=payload, headers=HEADERS)
            
        return response.json() if response.status_code in [200, 201] else None
    except Exception as e:
        print(f"Error sincronizando Paciente a HAPI FHIR: {e}")
        return None


def sync_device_to_fhir(equipo_data: dict):
    """Envía o actualiza un recurso Device (Equipo UCI / Hoja de Vida) en HAPI FHIR"""
    payload = {
        "resourceType": "Device",
        "identifier": [
            {
                "type": {"text": "Placa Inventario"},
                "value": equipo_data.get("codigo_inventario")
            },
            {
                "type": {"text": "Número Serie"},
                "value": equipo_data.get("numero_serie")
            },
            {
                "type": {"text": "Registro INVIMA"},
                "value": equipo_data.get("registro_invima")
            }
        ],
        "status": "active" if equipo_data.get("estado_operativo") == "En Uso" else "inactive",
        "manufacturer": equipo_data.get("marca"),
        "modelNumber": equipo_data.get("modelo"),
        "deviceName": [{
            "name": equipo_data.get("tipo_equipo", "Equipo Biomédico UCI"),
            "type": "user-friendly-name"
        }],
        "location": {
            "display": equipo_data.get("ubicacion_uci", "Unidad de Cuidados Intensivos")
        }
    }

    try:
        if equipo_data.get("fhir_id"):
            url = f"{HAPI_FHIR_URL}/Device/{equipo_data['fhir_id']}"
            response = requests.put(url, json=payload, headers=HEADERS)
        else:
            url = f"{HAPI_FHIR_URL}/Device"
            response = requests.post(url, json=payload, headers=HEADERS)
            
        return response.json() if response.status_code in [200, 201] else None
    except Exception as e:
        print(f"Error sincronizando Equipo (Device) a HAPI FHIR: {e}")
        return None


def sync_encounter_to_fhir(encuentro_data: dict):
    """Envía o actualiza un recurso Encounter en HAPI FHIR asociándolo al Paciente y Equipo UCI"""
    payload = {
        "resourceType": "Encounter",
        "status": encuentro_data.get("estado", "in-progress"),
        "class": {
            "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
            "code": "IMP",
            "display": "inpatient encounter (UCI)"
        },
        "subject": {
            "reference": f"Patient/{encuentro_data.get('paciente_fhir_id', encuentro_data.get('paciente_id'))}"
        }
    }

    if encuentro_data.get("equipo_uci_id"):
        payload["device"] = [
            {
                "device": {
                    "reference": f"Device/{encuentro_data.get('equipo_fhir_id', encuentro_data.get('equipo_uci_id'))}"
                }
            }
        ]

    try:
        if encuentro_data.get("fhir_id"):
            url = f"{HAPI_FHIR_URL}/Encounter/{encuentro_data['fhir_id']}"
            response = requests.put(url, json=payload, headers=HEADERS)
        else:
            url = f"{HAPI_FHIR_URL}/Encounter"
            response = requests.post(url, json=payload, headers=HEADERS)
            
        return response.json() if response.status_code in [200, 201] else None
    except Exception as e:
        print(f"Error sincronizando Encuentro a HAPI FHIR: {e}")
        return None


def enviar_observacion_a_fhir(encuentro_id_fhir, parametro, loinc_code, valor, unidad, alerta):
    """Envía un recurso Observation en estándar FHIR a HAPI FHIR vinculado al Encuentro"""
    payload = {
        "resourceType": "Observation",
        "status": "final",
        "category": [
            {
                "coding": [
                    {
                        "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                        "code": "vital-signs",
                        "display": "Vital Signs"
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
        "encounter": {
            "reference": f"Encounter/{encuentro_id_fhir}"
        },
        "valueQuantity": {
            "value": float(valor),
            "unit": unidad,
            "system": "http://unitsofmeasure.org"
        },
        "note": [{"text": alerta}] if alerta else []
    }

    try:
        response = requests.post(f"{HAPI_FHIR_URL}/Observation", json=payload, headers=HEADERS)
        return response.json() if response.status_code in [200, 201] else None
    except Exception as e:
        print(f"Error conectando a HAPI FHIR: {e}")
        return None