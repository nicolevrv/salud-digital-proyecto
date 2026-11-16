# agente/harness.py · El arnés completo del agente clínico y metrológico
import asyncio, threading
from collections import defaultdict
from datetime import datetime, timezone

from langchain.agents import create_agent
from langchain.agents.middleware import (AgentMiddleware, HumanInTheLoopMiddleware,
                                         ModelCallLimitMiddleware, ModelRequest, dynamic_prompt)
from langchain_core.messages import trim_messages

from agente import skills
from agente.herramientas import (ContextoClinico, calcular_imc, calcular_grasa_corporal,
                                 formatear_salida, consultar_paciente, consultar_telemetria_equipo,
                                 buscar_guias, cargar_skill, proponer_remision, guardar_preferencia,
                                 espacio_memoria, seudonimo)
from agente.modelo import crear_modelo
from agente.prompt import armar_prompt

HERRAMIENTAS = [
    calcular_imc,
    calcular_grasa_corporal,
    formatear_salida,
    consultar_paciente,
    consultar_telemetria_equipo,
    buscar_guias,
    cargar_skill,
    proponer_remision,
    guardar_preferencia
]


# ---------- 1. Prompt dinámico por capas (R03, Res. 866/2021) ----------
@dynamic_prompt
def prompt_clinico(request: ModelRequest) -> str:
    ctx = request.runtime.context
    preferencias = []
    if request.runtime.store is not None:
        guardadas = request.runtime.store.search(espacio_memoria(ctx.usuario))
        preferencias = [item.value["texto"] for item in guardadas]
    return armar_prompt(ctx, skills.indice(), preferencias)


# ---------- 2. Auditoría de trazabilidad en cada herramienta (R13, Res. 1995/1999) ----------
AUDITORIA: list[dict] = []

class Auditoria(AgentMiddleware):
    def _registrar(self, request):
        ctx = request.runtime.context
        AUDITORIA.append({
            "fecha": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "usuario": ctx.usuario,
            "rol": ctx.rol,
            "paciente": ctx.paciente_alias,
            "accion": "agente_herramienta",
            "herramienta": request.tool_call["name"],
            "args": request.tool_call["args"],
        })

    def wrap_tool_call(self, request, handler):
        self._registrar(request)
        return handler(request)

    async def awrap_tool_call(self, request, handler):
        self._registrar(request)
        return await handler(request)


# ---------- 3. Ventana de contexto (memoria de trabajo) ----------
class VentanaContexto(AgentMiddleware):
    def __init__(self, max_mensajes: int = 12):
        super().__init__()
        self.max_mensajes = max_mensajes

    def _recortar(self, request):
        recientes = trim_messages(request.messages, max_tokens=self.max_mensajes,
                                  token_counter=len, strategy="last", start_on="human")
        return request.override(messages=recientes)

    def wrap_model_call(self, request, handler):
        return handler(self._recortar(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._recortar(request))


# ---------- 4. Semáforo de límite concurrente al proveedor LLM ----------
class LimiteProveedor(AgentMiddleware):
    def __init__(self, maximo: int = 4):
        super().__init__()
        self.maximo = maximo
        self._sem_async = asyncio.Semaphore(maximo)
        self._sem_hilos = threading.Semaphore(maximo)

    def wrap_model_call(self, request, handler):
        with self._sem_hilos:
            return handler(request)

    async def awrap_model_call(self, request, handler):
        async with self._sem_async:
            return await handler(request)


# ---------- 5. Constructor del agente orquestado ----------
def construir_agente(checkpointer=None, store=None, latencia_simulada: float = 0.5,
                     max_simultaneas: int = 4):
    return create_agent(
        model=crear_modelo(latencia_simulada),
        tools=HERRAMIENTAS,
        context_schema=ContextoClinico,
        middleware=[
            prompt_clinico,
            VentanaContexto(max_mensajes=12),
            LimiteProveedor(maximo=max_simultaneas),
            ModelCallLimitMiddleware(run_limit=8),
            HumanInTheLoopMiddleware(interrupt_on={
                "proponer_remision": {"allowed_decisions": ["approve", "edit", "reject"]},
            }),
            Auditoria(),
        ],
        checkpointer=checkpointer,
        store=store,
    )


# ---------- 6. El harness que atiende solicitudes concurrentes ----------
from langgraph.types import Command

# Únicamente el Médico tiene la potestad clínica de aprobar o rechazar solicitudes del agente (HITL / R10 / R24)
ROLES_QUE_APRUEBAN = {"medico", "Medico"}

class Harness:
    def __init__(self, agente, timeout_s: float = 60):
        self.agente = agente
        self.timeout_s = timeout_s
        self._candados = defaultdict(asyncio.Lock)
        self._pendientes: dict[str, ContextoClinico] = {}

    @staticmethod
    def hilo(usuario: str, conversacion: str) -> str:
        return f"{usuario}:{conversacion}"

    def _empaquetar(self, salida, hilo: str) -> dict:
        if salida.interrupts:
            acciones = salida.interrupts[0].value["action_requests"]
            return {
                "estado": "pendiente_aprobacion",
                "conversacion": hilo,
                "acciones": [{"herramienta": a["name"], "args": a["args"]} for a in acciones]
            }
        self._pendientes.pop(hilo, None)
        return {
            "estado": "respondido",
            "conversacion": hilo,
            "respuesta": salida.value["messages"][-1].content
        }

    async def _ejecutar(self, entrada, ctx: ContextoClinico, hilo: str) -> dict:
        config = {"configurable": {"thread_id": hilo}}
        async with self._candados[hilo]:
            try:
                salida = await asyncio.wait_for(
                    self.agente.ainvoke(entrada, config, context=ctx, version="v2"),
                    timeout=self.timeout_s
                )
            except asyncio.TimeoutError:
                return {"estado": "tiempo_agotado", "conversacion": hilo}
        if salida.interrupts:
            self._pendientes[hilo] = ctx
        return self._empaquetar(salida, hilo)

    async def atender(self, ctx: ContextoClinico, mensaje: str, conversacion: str = "principal") -> dict:
        hilo = self.hilo(ctx.usuario, conversacion)
        if hilo in self._pendientes:
            return {
                "estado": "pendiente_aprobacion",
                "conversacion": hilo,
                "detalle": "Hay una acción clínica esperando decisión humana."
            }
        return await self._ejecutar({"messages": [{"role": "user", "content": mensaje}]}, ctx, hilo)

    async def decidir(self, usuario: str, rol: str, conversacion: str,
                      decision: str, motivo: str | None = None) -> dict:
        hilo = self.hilo(usuario, conversacion)
        ctx = self._pendientes.get(hilo)
        if ctx is None:
            return {"estado": "sin_pendientes", "conversacion": hilo}
        if rol not in ROLES_QUE_APRUEBAN:
            return {"estado": "no_autorizado", "conversacion": hilo}
        d = {"type": decision}
        if decision == "reject" and motivo:
            d["message"] = motivo
        return await self._ejecutar(Command(resume={"decisions": [d]}), ctx, hilo)
