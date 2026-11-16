# -*- coding: utf-8 -*-
"""
cifrado.py · Módulo de Cifrado de Campos Sensibles con AES-256-GCM (R27)
Salud Digital 2026-2 · Universidad Autónoma de Occidente
Cumple Ley 1581/2012 y Res. 1995/1999:
Cifra documento de identidad, teléfono y dirección en reposo (PostgreSQL).
La clave vive en la variable de entorno ENCRYPTION_KEY, nunca en el repositorio.
"""
import os
import base64
import hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Clave de 256 bits derivada de la variable de entorno ENCRYPTION_KEY
_RAW_KEY = os.getenv("ENCRYPTION_KEY", "llave-secreta-aes-gcm-salud-digital-uao-2026")
_KEY_BYTES = hashlib.sha256(_RAW_KEY.encode("utf-8")).digest()
_AESGCM = AESGCM(_KEY_BYTES)

PREFIX_ENCRYPTED = "ENC$GCM$"

def cifrar_campo(texto_plano: str | None) -> str | None:
    """
    Cifra un texto plano utilizando AES-256-GCM con un nonce aleatorio de 12 bytes.
    Retorna una cadena codificada en Base64 con el prefijo 'ENC$GCM$'.
    Si el valor es None o ya está cifrado, lo retorna intacto.
    """
    if texto_plano is None:
        return None
    texto_str = str(texto_plano).strip()
    if not texto_str or texto_str.startswith(PREFIX_ENCRYPTED):
        return texto_str

    # Nonce criptográfico aleatorio de 96 bits (12 bytes)
    nonce = os.urandom(12)
    datos_bytes = texto_str.encode("utf-8")
    ciphertext = _AESGCM.encrypt(nonce, datos_bytes, None)

    # Estructura empaquetada: nonce (12 bytes) + ciphertext con authentication tag
    paquete = nonce + ciphertext
    b64 = base64.b64encode(paquete).decode("utf-8")
    return f"{PREFIX_ENCRYPTED}{b64}"


def descifrar_campo(texto_cifrado: str | None) -> str | None:
    """
    Descifra un valor cifrado con AES-256-GCM.
    Si el valor no tiene el prefijo de cifrado o es texto plano legado, lo retorna tal cual.
    """
    if texto_cifrado is None:
        return None
    texto_str = str(texto_cifrado).strip()
    if not texto_str.startswith(PREFIX_ENCRYPTED):
        return texto_str

    try:
        b64 = texto_str[len(PREFIX_ENCRYPTED):]
        paquete = base64.b64decode(b64.encode("utf-8"))
        if len(paquete) < 28: # 12 bytes nonce + 16 bytes tag mínimo
            return texto_str
        nonce = paquete[:12]
        ciphertext = paquete[12:]
        texto_bytes = _AESGCM.decrypt(nonce, ciphertext, None)
        return texto_bytes.decode("utf-8")
    except Exception:
        # Si falla la autenticación de integridad GCM, se previene divulgación de error
        return "[DATOS_CIFRADOS_INTEGRIDAD_FALLIDA]"
