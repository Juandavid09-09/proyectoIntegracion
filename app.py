from flask import Flask, request, jsonify
from datetime import datetime
import uuid
import os
import json
from urllib.request import Request, urlopen

aplicacion = Flask(__name__)

# ─────────────────────────────────────────────────────────────
#  CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────

CLAVE_API             = os.environ.get("API_KEY", "CECAR-DEMO-KEY")
MAKE_WEBHOOK_SHEETS   = os.environ.get("MAKE_WEBHOOK_SHEETS", "").strip()
MAKE_WEBHOOK_CORREOS  = os.environ.get("MAKE_WEBHOOK_CORREOS", "").strip()

# ─────────────────────────────────────────────────────────────
#  ALMACENAMIENTO EN MEMORIA
# ─────────────────────────────────────────────────────────────

SOLICITUDES = []
EVENTOS     = []
ASESORIAS   = []

# ─────────────────────────────────────────────────────────────
#  SEMÁNTICA: simple vs compleja
# ─────────────────────────────────────────────────────────────
#
#  SIMPLE  → urgencia baja
#            O urgencia media + descripción < 200 chars
#            Acción: respuesta directa del docente
#
#  COMPLEJA → urgencia alta
#             O urgencia media + descripción >= 200 chars
#             Acción: se agenda asesoría formal

def clasificar_solicitud(descripcion: str, urgencia: str) -> str:
    if urgencia == "alta":
        return "compleja"
    if urgencia == "media" and len(descripcion.strip()) >= 200:
        return "compleja"
    return "simple"

# ─────────────────────────────────────────────────────────────
#  UTILIDADES
# ─────────────────────────────────────────────────────────────

def ahora_iso():
    return datetime.now().isoformat(timespec="seconds")


def respuesta_error(codigo_http, codigo_error, mensaje, detalles=None):
    return jsonify({
        "ok": False,
        "codigo_error": codigo_error,
        "mensaje": mensaje,
        "detalles": detalles or {}
    }), codigo_http


def enviar_webhook(url: str, payload: dict):
    """Envía un JSON a una URL de webhook."""
    if not url:
        return
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req  = Request(url, data=data,
                       headers={"Content-Type": "application/json"},
                       method="POST")
        urlopen(req, timeout=6).read()
    except Exception as e:
        print(f"[WEBHOOK] Error → {url}: {e}")


def registrar_evento(tipo: str, id_solicitud: str, payload: dict) -> dict:
    """Guarda el evento y lo envía al webhook de Sheets."""
    evento = {
        "id_evento":    f"EVT-{str(uuid.uuid4())[:8].upper()}",
        "tipo":         tipo,
        "id_solicitud": id_solicitud,
        "timestamp":    ahora_iso(),
        **payload
    }
    EVENTOS.append(evento)
    enviar_webhook(MAKE_WEBHOOK_SHEETS, evento)
    return evento

# ─────────────────────────────────────────────────────────────
#  VALIDACIÓN — contrato JSON
# ─────────────────────────────────────────────────────────────
#
#  Campo               Tipo    Descripción
#  nombre_estudiante   string  Nombre completo del estudiante
#  email_estudiante    string  Correo del estudiante
#  curso               string  Nombre de la asignatura
#  tema                string  Tema puntual de la duda
#  descripcion         string  Explicación detallada
#  urgencia            enum    "baja" | "media" | "alta"

URGENCIAS_VALIDAS = {"baja", "media", "alta"}

def validar_solicitud(datos: dict):
    for campo in ["nombre_estudiante", "email_estudiante",
                  "curso", "tema", "descripcion", "urgencia"]:
        if campo not in datos:
            return False, f"Falta el campo obligatorio: '{campo}'"

    if not isinstance(datos["nombre_estudiante"], str) or not datos["nombre_estudiante"].strip():
        return False, "'nombre_estudiante' debe ser texto no vacío"
    if not isinstance(datos["email_estudiante"], str) or "@" not in datos["email_estudiante"]:
        return False, "'email_estudiante' debe ser un correo válido"
    if not isinstance(datos["curso"], str) or not datos["curso"].strip():
        return False, "'curso' debe ser texto no vacío"
    if not isinstance(datos["tema"], str) or not datos["tema"].strip():
        return False, "'tema' debe ser texto no vacío"
    if not isinstance(datos["descripcion"], str) or not datos["descripcion"].strip():
        return False, "'descripcion' debe ser texto no vacío"
    if datos["urgencia"] not in URGENCIAS_VALIDAS:
        return False, f"'urgencia' debe ser: {', '.join(sorted(URGENCIAS_VALIDAS))}"

    return True, None


def autenticar(req) -> bool:
    return req.headers.get("X-API-key", "") == CLAVE_API

# ─────────────────────────────────────────────────────────────
#  ENDPOINTS
# ─────────────────────────────────────────────────────────────

@aplicacion.get("/")
def inicio():
    return jsonify({
        "servicio": "Sistema de Asesorías Académicas – CECAR",
        "version":  "2.0.0",
        "auth":     "Header: X-API-key: CECAR-DEMO-KEY",
        "endpoints": {
            "POST /api/v1/solicitud":   "Registrar solicitud académica",
            "GET  /api/v1/solicitudes": "Listar solicitudes",
            "GET  /api/v1/eventos":     "Log de eventos",
            "GET  /api/v1/asesorias":   "Asesorías programadas",
            "GET  /api/v1/metricas":    "Métricas del sistema"
        }
    })


@aplicacion.post("/api/v1/solicitud")
def recibir_solicitud():
    if not autenticar(request):
        return respuesta_error(401, "NO_AUTORIZADO",
                               "Header X-API-key ausente o incorrecto.")

    datos = request.get_json(silent=True) or {}
    es_valido, razon = validar_solicitud(datos)
    if not es_valido:
        return respuesta_error(400, "FORMATO_INVALIDO",
                               "El JSON no cumple el contrato.",
                               {"razon": razon})

    # ── Crear solicitud ──────────────────────────────────────
    id_solicitud = f"SOL-{str(uuid.uuid4())[:8].upper()}"
    solicitud = {
        "id_solicitud":      id_solicitud,
        "nombre_estudiante": datos["nombre_estudiante"].strip(),
        "email_estudiante":  datos["email_estudiante"].strip().lower(),
        "curso":             datos["curso"].strip(),
        "tema":              datos["tema"].strip(),
        "descripcion":       datos["descripcion"].strip(),
        "urgencia":          datos["urgencia"],
        "recibido_en":       ahora_iso(),
        "estado":            "pendiente"
    }
    SOLICITUDES.append(solicitud)

    # ── EVENTO 1: solicitud_creada ───────────────────────────
    registrar_evento("solicitud_creada", id_solicitud, {
        "nombre_estudiante": solicitud["nombre_estudiante"],
        "email_estudiante":  solicitud["email_estudiante"],
        "curso":             solicitud["curso"],
        "tema":              solicitud["tema"],
        "urgencia":          solicitud["urgencia"]
    })

    # ── Clasificar ───────────────────────────────────────────
    tipo = clasificar_solicitud(solicitud["descripcion"], solicitud["urgencia"])
    solicitud["tipo_consulta"] = tipo

    # ── EVENTO 2: solicitud_clasificada ─────────────────────
    registrar_evento("solicitud_clasificada", id_solicitud, {
        "tipo_consulta":     tipo,
        "nombre_estudiante": solicitud["nombre_estudiante"],
        "email_estudiante":  solicitud["email_estudiante"],
        "curso":             solicitud["curso"],
        "tema":              solicitud["tema"],
        "urgencia":          solicitud["urgencia"]
    })

    # ── WEBHOOK CORREOS: payload limpio y garantizado ────────
    # Se envía SOLO cuando la clasificación está completa.
    # Todos los campos están siempre presentes → Make no falla.
    payload_correo = {
        "tipo_consulta":     tipo,              # "simple" o "compleja"
        "nombre_estudiante": solicitud["nombre_estudiante"],
        "email_estudiante":  solicitud["email_estudiante"],
        "curso":             solicitud["curso"],
        "tema":              solicitud["tema"],
        "urgencia":          solicitud["urgencia"],
        "id_solicitud":      id_solicitud,
        "timestamp":         ahora_iso()
    }
    enviar_webhook(MAKE_WEBHOOK_CORREOS, payload_correo)

    # ── RAMA SIMPLE ──────────────────────────────────────────
    if tipo == "simple":
        solicitud["estado"] = "respondida"

        respuesta_texto = (
            f"Hola {solicitud['nombre_estudiante']}, tu consulta sobre "
            f"'{solicitud['tema']}' en '{solicitud['curso']}' fue recibida. "
            f"Un docente te responderá directamente en breve. "
            f"[Urgencia: {solicitud['urgencia']}]"
        )

        # ── EVENTO 3: respuesta_directa_enviada ─────────────
        registrar_evento("respuesta_directa_enviada", id_solicitud, {
            "nombre_estudiante": solicitud["nombre_estudiante"],
            "email_estudiante":  solicitud["email_estudiante"],
            "tema":              solicitud["tema"],
            "curso":             solicitud["curso"],
            "respuesta":         respuesta_texto
        })

        return jsonify({
            "ok":                  True,
            "id_solicitud":        id_solicitud,
            "solicitud":           solicitud,
            "clasificacion":       "simple",
            "accion":              "respuesta_directa",
            "respuesta_docente":   respuesta_texto,
            "asesoria_programada": False
        }), 201

    # ── RAMA COMPLEJA ────────────────────────────────────────
    else:
        solicitud["estado"] = "asesoria_programada"

        id_asesoria = f"ASE-{str(uuid.uuid4())[:8].upper()}"
        asesoria = {
            "id_asesoria":       id_asesoria,
            "id_solicitud":      id_solicitud,
            "nombre_estudiante": solicitud["nombre_estudiante"],
            "email_estudiante":  solicitud["email_estudiante"],
            "curso":             solicitud["curso"],
            "tema":              solicitud["tema"],
            "urgencia":          solicitud["urgencia"],
            "estado":            "programada",
            "creada_en":         ahora_iso()
        }
        ASESORIAS.append(asesoria)

        # ── EVENTO 4: requiere_asesoria ──────────────────────
        registrar_evento("requiere_asesoria", id_solicitud, {
            "nombre_estudiante": solicitud["nombre_estudiante"],
            "email_estudiante":  solicitud["email_estudiante"],
            "curso":             solicitud["curso"],
            "tema":              solicitud["tema"],
            "urgencia":          solicitud["urgencia"],
            "id_asesoria":       id_asesoria
        })

        # ── EVENTO 5: asesoria_programada ────────────────────
        registrar_evento("asesoria_programada", id_solicitud, {
            "id_asesoria":       id_asesoria,
            "nombre_estudiante": solicitud["nombre_estudiante"],
            "email_estudiante":  solicitud["email_estudiante"],
            "curso":             solicitud["curso"],
            "tema":              solicitud["tema"],
            "urgencia":          solicitud["urgencia"]
        })

        return jsonify({
            "ok":                  True,
            "id_solicitud":        id_solicitud,
            "solicitud":           solicitud,
            "clasificacion":       "compleja",
            "accion":              "asesoria_programada",
            "id_asesoria":         id_asesoria,
            "mensaje": (
                f"Tu solicitud requiere asesoría formal. "
                f"Se creó la asesoría {id_asesoria}. "
                f"El docente será notificado para confirmar el horario."
            ),
            "asesoria_programada": True
        }), 201


@aplicacion.get("/api/v1/solicitudes")
def listar_solicitudes():
    if not autenticar(request):
        return respuesta_error(401, "NO_AUTORIZADO",
                               "Header X-API-key ausente o incorrecto.")
    return jsonify({"ok": True, "total": len(SOLICITUDES),
                    "solicitudes": SOLICITUDES})


@aplicacion.get("/api/v1/eventos")
def listar_eventos():
    if not autenticar(request):
        return respuesta_error(401, "NO_AUTORIZADO",
                               "Header X-API-key ausente o incorrecto.")
    return jsonify({"ok": True, "total_eventos": len(EVENTOS),
                    "eventos": EVENTOS})


@aplicacion.get("/api/v1/asesorias")
def listar_asesorias():
    if not autenticar(request):
        return respuesta_error(401, "NO_AUTORIZADO",
                               "Header X-API-key ausente o incorrecto.")
    return jsonify({"ok": True, "total": len(ASESORIAS),
                    "asesorias": ASESORIAS})


@aplicacion.get("/api/v1/metricas")
def metricas():
    if not autenticar(request):
        return respuesta_error(401, "NO_AUTORIZADO",
                               "Header X-API-key ausente o incorrecto.")

    simples   = [s for s in SOLICITUDES if s.get("tipo_consulta") == "simple"]
    complejas = [s for s in SOLICITUDES if s.get("tipo_consulta") == "compleja"]

    por_curso = {}
    for s in SOLICITUDES:
        c = s.get("curso", "desconocido")
        por_curso[c] = por_curso.get(c, 0) + 1

    por_urgencia = {"baja": 0, "media": 0, "alta": 0}
    for s in SOLICITUDES:
        u = s.get("urgencia", "baja")
        por_urgencia[u] = por_urgencia.get(u, 0) + 1

    por_evento = {}
    for e in EVENTOS:
        t = e.get("tipo", "desconocido")
        por_evento[t] = por_evento.get(t, 0) + 1

    return jsonify({
        "ok": True,
        "metricas": {
            "total_solicitudes":        len(SOLICITUDES),
            "consultas_simples":        len(simples),
            "consultas_complejas":      len(complejas),
            "asesorias_programadas":    len(ASESORIAS),
            "total_eventos_generados":  len(EVENTOS),
            "solicitudes_por_curso":    por_curso,
            "solicitudes_por_urgencia": por_urgencia,
            "eventos_por_tipo":         por_evento
        }
    })


# ─────────────────────────────────────────────────────────────
#  ARRANQUE
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 3000))
    aplicacion.run(host="0.0.0.0", port=puerto)
