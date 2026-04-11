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

CLAVE_API            = os.environ.get("API_KEY", "CECAR-DEMO-KEY")
MAKE_WEBHOOK_URL     = os.environ.get("MAKE_WEBHOOK_URL", "").strip()
MAKE_WEBHOOK_CORREOS = os.environ.get("MAKE_WEBHOOK_CORREOS", "").strip()

# ─────────────────────────────────────────────────────────────
#  ALMACENAMIENTO EN MEMORIA
# ─────────────────────────────────────────────────────────────

SOLICITUDES = []   # todas las solicitudes registradas
EVENTOS     = []   # log de eventos del sistema
ASESORIAS   = []   # asesorías programadas

# ─────────────────────────────────────────────────────────────
#  INTEROPERABILIDAD SEMÁNTICA
#  Definición justificada de consulta simple vs compleja
# ─────────────────────────────────────────────────────────────
#
#  CONSULTA SIMPLE
#    Duda puntual que un docente puede resolver con texto.
#    Criterio: urgencia "baja"
#              O urgencia "media" con descripción corta (< 200 chars)
#    Acción  : respuesta directa — sin agendar asesoría.
#
#  CONSULTA COMPLEJA
#    Problema profundo que requiere acompañamiento formal.
#    Criterio: urgencia "alta"
#              O urgencia "media" con descripción larga (>= 200 chars)
#    Justificación: una descripción extensa indica que el estudiante
#    ya intentó resolver la duda por su cuenta y no lo logró; sumado
#    a urgencia media/alta, la situación amerita una sesión dedicada.
#    Acción  : programar asesoría y notificar al docente.

def clasificar_solicitud(descripcion: str, urgencia: str) -> str:
    """Retorna 'simple' o 'compleja' según las reglas semánticas."""
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


def respuesta_error(codigo_http: int, codigo_error: str, mensaje: str, detalles=None):
    return jsonify({
        "ok": False,
        "codigo_error": codigo_error,
        "mensaje": mensaje,
        "detalles": detalles or {}
    }), codigo_http


def registrar_evento(tipo: str, id_solicitud: str, payload: dict) -> dict:
    """
    Crea un evento, lo guarda en memoria y lo reenvía a Make.
    Los campos del payload se elevan a la raíz del JSON para que
    Make pueda leerlos directamente con {{1.campo}} sin Parse JSON.
    Tipos obligatorios según el proyecto:
      solicitud_creada | solicitud_clasificada |
      respuesta_directa_enviada | requiere_asesoria | asesoria_programada
    """
    evento = {
        "id_evento":    f"EVT-{str(uuid.uuid4())[:8].upper()}",
        "tipo":         tipo,
        "id_solicitud": id_solicitud,
        "timestamp":    ahora_iso(),
        "payload":      payload,
        **payload
    }
    EVENTOS.append(evento)
    enviar_a_make(evento)
    return evento


def enviar_a_make(payload: dict):
    """Envía el evento a los dos webhooks de Make configurados."""
    for url in [MAKE_WEBHOOK_URL, MAKE_WEBHOOK_CORREOS]:
        if not url:
            continue
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req  = Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urlopen(req, timeout=6).read()
        except Exception as e:
            print(f"[MAKE] Error al enviar a {url}: {e}")

# ─────────────────────────────────────────────────────────────
#  VALIDACIÓN — INTEROPERABILIDAD SINTÁCTICA
#  Contrato JSON con definición clara de campos
# ─────────────────────────────────────────────────────────────
#
#  Campo               Tipo    Descripción
#  ──────────────────────────────────────────────────────────
#  nombre_estudiante   string  Nombre completo del estudiante
#  email_estudiante    string  Correo electrónico del estudiante
#  curso               string  Nombre o código de la asignatura
#  tema                string  Tema puntual de la duda
#  descripcion         string  Explicación detallada de la duda
#  urgencia            enum    "baja" | "media" | "alta"

NIVELES_URGENCIA_VALIDOS = {"baja", "media", "alta"}

def validar_solicitud(datos: dict):
    campos = ["nombre_estudiante", "email_estudiante", "curso", "tema", "descripcion", "urgencia"]
    for campo in campos:
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

    if datos["urgencia"] not in NIVELES_URGENCIA_VALIDOS:
        return False, f"'urgencia' debe ser uno de: {', '.join(sorted(NIVELES_URGENCIA_VALIDOS))}"

    return True, None


def autenticar(req) -> bool:
    return req.headers.get("X-API-key", "") == CLAVE_API

# ─────────────────────────────────────────────────────────────
#  ENDPOINTS — INTEROPERABILIDAD TÉCNICA (API REST)
# ─────────────────────────────────────────────────────────────

@aplicacion.get("/")
def inicio():
    """Punto de entrada informativo del servicio."""
    return jsonify({
        "servicio": "Sistema de Asesorías Académicas – CECAR",
        "version":  "1.0.0",
        "auth":     "Header requerido → X-API-key: CECAR-DEMO-KEY",
        "endpoints": {
            "POST /api/v1/solicitud":   "Registrar nueva solicitud académica",
            "GET  /api/v1/solicitudes": "Listar todas las solicitudes",
            "GET  /api/v1/eventos":     "Ver log de eventos del sistema",
            "GET  /api/v1/asesorias":   "Ver asesorías programadas",
            "GET  /api/v1/metricas":    "Métricas generales del sistema"
        }
    })


# ── POST /api/v1/solicitud ────────────────────────────────────
@aplicacion.post("/api/v1/solicitud")
def recibir_solicitud():
    """
    Registra una solicitud académica, la clasifica automáticamente
    y genera los 5 eventos obligatorios del sistema.
    Si es compleja, programa una asesoría formal.
    """
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
        "curso":    solicitud["curso"],
        "tema":     solicitud["tema"],
        "urgencia": solicitud["urgencia"]
    })

    # ── Clasificar solicitud ─────────────────────────────────
    tipo = clasificar_solicitud(solicitud["descripcion"], solicitud["urgencia"])
    solicitud["tipo_consulta"] = tipo

    # ── EVENTO 2: solicitud_clasificada ─────────────────────
    registrar_evento("solicitud_clasificada", id_solicitud, {
        "tipo_consulta":     tipo,
        "nombre_estudiante": solicitud["nombre_estudiante"],
        "email_estudiante":  solicitud["email_estudiante"],
        "curso":             solicitud["curso"],
        "tema":              solicitud["tema"],
        "urgencia":          solicitud["urgencia"],
        "descripcion":       solicitud["descripcion"]
    })

    # ── RAMA SIMPLE ──────────────────────────────────────────
    if tipo == "simple":
        solicitud["estado"] = "respondida"

        respuesta_texto = (
            f"Hola {solicitud['nombre_estudiante']}, tu consulta sobre "
            f"'{solicitud['tema']}' en '{solicitud['curso']}' fue recibida. "
            f"Un docente te responderá directamente en breve. "
            f"[Urgencia registrada: {solicitud['urgencia']}]"
        )

        # ── EVENTO 3: respuesta_directa_enviada ─────────────
        registrar_evento("respuesta_directa_enviada", id_solicitud, {
            "nombre_estudiante": solicitud["nombre_estudiante"],
            "email_estudiante":  solicitud["email_estudiante"],
            "tema":              solicitud["tema"],
            "curso":             solicitud["curso"],
            "respuesta":         respuesta_texto,
            "canal":             "sistema"
        })

        return jsonify({
            "ok":                 True,
            "id_solicitud":       id_solicitud,
            "solicitud":          solicitud,
            "clasificacion":      "simple",
            "accion":             "respuesta_directa",
            "respuesta_docente":  respuesta_texto,
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
            "creada_en":         ahora_iso(),
            "nota":              "Pendiente de confirmación de horario por el docente."
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
            "ok":             True,
            "id_solicitud":   id_solicitud,
            "solicitud":      solicitud,
            "clasificacion":  "compleja",
            "accion":         "asesoria_programada",
            "id_asesoria":    id_asesoria,
            "mensaje": (
                f"Tu solicitud requiere asesoría formal. "
                f"Se creó la asesoría {id_asesoria}. "
                f"El docente será notificado para confirmar el horario."
            ),
            "asesoria_programada": True
        }), 201


# ── GET /api/v1/solicitudes ───────────────────────────────────
@aplicacion.get("/api/v1/solicitudes")
def listar_solicitudes():
    """Devuelve todas las solicitudes registradas."""
    if not autenticar(request):
        return respuesta_error(401, "NO_AUTORIZADO",
                               "Header X-API-key ausente o incorrecto.")
    return jsonify({
        "ok":          True,
        "total":       len(SOLICITUDES),
        "solicitudes": SOLICITUDES
    })


# ── GET /api/v1/eventos ───────────────────────────────────────
@aplicacion.get("/api/v1/eventos")
def listar_eventos():
    """Devuelve el log completo de eventos del sistema."""
    if not autenticar(request):
        return respuesta_error(401, "NO_AUTORIZADO",
                               "Header X-API-key ausente o incorrecto.")
    return jsonify({
        "ok":            True,
        "total_eventos": len(EVENTOS),
        "eventos":       EVENTOS
    })


# ── GET /api/v1/asesorias ─────────────────────────────────────
@aplicacion.get("/api/v1/asesorias")
def listar_asesorias():
    """Devuelve todas las asesorías programadas."""
    if not autenticar(request):
        return respuesta_error(401, "NO_AUTORIZADO",
                               "Header X-API-key ausente o incorrecto.")
    return jsonify({
        "ok":        True,
        "total":     len(ASESORIAS),
        "asesorias": ASESORIAS
    })


# ── GET /api/v1/metricas ──────────────────────────────────────
@aplicacion.get("/api/v1/metricas")
def metricas():
    """Métricas generales — cubre el bonus de dashboard de seguimiento."""
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
        t = e["tipo"]
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
#  ARRANQUE LOCAL
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    puerto = int(os.environ.get("PORT", 3000))
    aplicacion.run(host="0.0.0.0", port=puerto)
