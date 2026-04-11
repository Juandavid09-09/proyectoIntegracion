from flask import Flask, request, jsonify
from datetime import datetime
import uuid
import os
import json
from urllib.request import Request, urlopen

aplicacion = Flask(__name__)

CLAVE_API = os.environ.get("CLAVE_API", "CECAR-DEMO-KEY")
MAKE_WEBHOOK_URL = os.environ.get("MAKE_WEBHOOK_URL", "").strip()

SOLICITUDES = []

URGENCIAS_VALIDAS = {"bajo", "medio", "alto"}

TEMAS_CRITICOS = {
    "parcial", "examen", "final", "proyecto", "tesis",
    "sustentacion", "calculo", "programacion", "algoritmos",
    "base de datos", "estadistica",
}


def momento_actual():
    return datetime.now().isoformat(timespec="seconds")


def fecha_legible():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")


def error_json(codigo_http, codigo_error, mensaje, detalles=None):
    return jsonify({
        "ok": False,
        "codigo_error": codigo_error,
        "mensaje": mensaje,
        "detalles": detalles or {}
    }), codigo_http


def disparar_webhook(payload: dict):
    if not MAKE_WEBHOOK_URL:
        return
    try:
        datos = json.dumps(payload).encode("utf-8")
        peticion = Request(
            MAKE_WEBHOOK_URL,
            data=datos,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        urlopen(peticion, timeout=6).read()
    except Exception as e:
        print("Fallo al contactar Make:", str(e))


def validar_campos(datos: dict):
    obligatorios = [
        "nombre_estudiante", "correo_estudiante", "curso",
        "tema", "descripcion", "urgencia", "marca_tiempo"
    ]
    for campo in obligatorios:
        if campo not in datos:
            return False, f"Campo requerido ausente: '{campo}'"

    if not isinstance(datos["nombre_estudiante"], str) or not datos["nombre_estudiante"].strip():
        return False, "'nombre_estudiante' no puede estar vacío"
    if not isinstance(datos["correo_estudiante"], str) or "@" not in datos["correo_estudiante"]:
        return False, "'correo_estudiante' no tiene formato válido"
    if not isinstance(datos["curso"], str) or not datos["curso"].strip():
        return False, "'curso' no puede estar vacío"
    if not isinstance(datos["tema"], str) or not datos["tema"].strip():
        return False, "'tema' no puede estar vacío"
    if not isinstance(datos["descripcion"], str) or not datos["descripcion"].strip():
        return False, "'descripcion' no puede estar vacía"
    if datos["urgencia"] not in URGENCIAS_VALIDAS:
        return False, f"'urgencia' debe ser: {', '.join(sorted(URGENCIAS_VALIDAS))}"
    if not isinstance(datos["marca_tiempo"], str):
        return False, "'marca_tiempo' debe tener formato ISO 8601"

    return True, None


def clasificar(tema: str, urgencia: str) -> dict:
    tema_lower = tema.lower().strip()
    tema_critico = any(p in tema_lower for p in TEMAS_CRITICOS)
    urgencia_maxima = urgencia == "alto"

    if urgencia_maxima and tema_critico:
        return {
            "tipo": "compleja",
            "accion": "requiere_asesoria",
            "descripcion": "La solicitud requiere asesoría formal con el docente."
        }
    return {
        "tipo": "simple",
        "accion": "respuesta_directa",
        "descripcion": "La solicitud puede resolverse con una respuesta directa."
    }


def construir_eventos(clasificacion: dict, id_solicitud: str, datos: dict) -> list:
    ts = momento_actual()
    eventos = [
        {
            "evento": "solicitud_creada",
            "id_solicitud": id_solicitud,
            "timestamp": ts,
            "datos": {
                "estudiante": datos["nombre_estudiante"],
                "curso": datos["curso"],
                "tema": datos["tema"],
                "urgencia": datos["urgencia"]
            }
        },
        {
            "evento": "solicitud_clasificada",
            "id_solicitud": id_solicitud,
            "timestamp": ts,
            "clasificacion": clasificacion["tipo"],
            "accion_a_tomar": clasificacion["accion"]
        }
    ]

    if clasificacion["tipo"] == "simple":
        eventos.append({
            "evento": "respuesta_directa_enviada",
            "id_solicitud": id_solicitud,
            "timestamp": ts,
            "mensaje": "El docente puede responder sin agendar asesoría."
        })
    else:
        eventos.append({
            "evento": "requiere_asesoria",
            "id_solicitud": id_solicitud,
            "timestamp": ts,
            "motivo": f"Urgencia alta + tema crítico detectado: {datos['tema']}"
        })
        eventos.append({
            "evento": "asesoria_programada",
            "id_solicitud": id_solicitud,
            "timestamp": ts,
            "docente_notificado": True,
            "correo_estudiante": datos["correo_estudiante"]
        })

    return eventos


@aplicacion.get("/")
def inicio():
    return jsonify({
        "servicio": "Sistema de Asesorías Académicas – CECAR",
        "version": "1.0.0",
        "autenticacion": "Header requerido → X-API-key: CECAR-DEMO-KEY",
        "endpoints": {
            "POST /api/v1/solicitud": "Registra y clasifica una solicitud académica",
            "GET  /api/v1/solicitudes": "Consulta todas las solicitudes almacenadas"
        }
    })


@aplicacion.post("/api/v1/solicitud")
def recibir_solicitud():
    clave = request.headers.get("X-API-key", "")
    if clave != CLAVE_API:
        return error_json(401, "NO_AUTORIZADO", "Clave de API ausente o incorrecta.")

    datos = request.get_json(silent=True) or {}
    valido, razon = validar_campos(datos)
    if not valido:
        return error_json(400, "DATOS_INVALIDOS", "El cuerpo no cumple el contrato esperado.", {"razon": razon})

    id_solicitud = f"SOL-{str(uuid.uuid4())[:8].upper()}"
    ts = momento_actual()
    fecha = fecha_legible()

    solicitud = {
        "id_solicitud": id_solicitud,
        "recibido_en": ts,
        **datos
    }
    SOLICITUDES.append(solicitud)

    clasificacion = clasificar(datos["tema"], datos["urgencia"])
    eventos = construir_eventos(clasificacion, id_solicitud, datos)

    payload_make = {
        "id_solicitud": id_solicitud,
        "fecha_registro": fecha,
        "timestamp": ts,
        "nombre_estudiante": datos["nombre_estudiante"],
        "correo_estudiante": datos["correo_estudiante"],
        "curso": datos["curso"],
        "tema": datos["tema"],
        "descripcion": datos["descripcion"],
        "urgencia": datos["urgencia"],
        "clasificacion": clasificacion["tipo"],
        "accion": clasificacion["accion"],
        "descripcion_clasificacion": clasificacion["descripcion"],
        "total_eventos": len(eventos),
        "nombres_eventos": [e["evento"] for e in eventos],
        "estado_asesoria": "pendiente" if clasificacion["tipo"] == "compleja" else "no_aplica",
        "docente_notificado": clasificacion["tipo"] == "compleja",
        "total_solicitudes_sistema": len(SOLICITUDES)
    }
    disparar_webhook(payload_make)

    return jsonify({
        "ok": True,
        "id_solicitud": id_solicitud,
        "solicitud_almacenada": solicitud,
        "clasificacion": clasificacion,
        "eventos_generados": eventos,
        "total_solicitudes": len(SOLICITUDES)
    }), 201


@aplicacion.get("/api/v1/solicitudes")
def listar_solicitudes():
    clave = request.headers.get("X-API-key", "")
    if clave != CLAVE_API:
        return error_json(401, "NO_AUTORIZADO", "Clave de API ausente o incorrecta.")

    return jsonify({
        "ok": True,
        "total": len(SOLICITUDES),
        "solicitudes": SOLICITUDES
    })


if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 3000))
    aplicacion.run(host="0.0.0.0", port=puerto)
