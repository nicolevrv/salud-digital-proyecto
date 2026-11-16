# agente/simulado.py · Modelo de práctica: imita el FORMATO de un LLM real
import asyncio, re, time, uuid
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

ESPECIALIDADES = {
    "oftalmolog": "oftalmologia",
    "endocrinolog": "endocrinologia",
    "nefrolog": "nefrologia",
    "cardiolog": "cardiologia",
    "nutrici": "nutricion",
    "biomed": "ingenieria_biomedica",
}

class ModeloSimulado(BaseChatModel):
    latencia: float = 0.5
    herramientas: list = []

    @property
    def _llm_type(self) -> str:
        return "simulado"

    def bind_tools(self, tools, **kwargs):
        nombres = [convert_to_openai_tool(t)["function"]["name"] for t in tools]
        return self.model_copy(update={"herramientas": nombres})

    def _plan(self, texto: str) -> list:
        t, plan = texto.lower(), []
        def usar(nombre, args):
            if nombre in self.herramientas:
                plan.append((nombre, args))

        # Extracción de peso y talla
        peso_match = re.search(r"(\d+(?:[.,]\d+)?)\s*kg", t)
        talla_match = re.search(r"(\d+[.,]\d+)\s*m\b", t)
        peso = float(peso_match.group(1).replace(",", ".")) if peso_match else None
        talla = float(talla_match.group(1).replace(",", ".")) if talla_match else None

        # Tool 1: IMC
        if "imc" in t or "masa corporal" in t:
            if peso and talla:
                usar("calcular_imc", {"peso_kg": peso, "talla_m": talla})

        # Tool 2: Grasa Corporal
        if "grasa" in t or "porcentaje de grasa" in t or "composici" in t:
            edad_match = re.search(r"(\d+)\s*(?:a[ñn]os|edad)", t)
            edad = int(edad_match.group(1)) if edad_match else 40
            sexo = "M" if any(s in t for s in ["hombre", "masculino", "varon", " varón", " m "]) else "F"
            if peso and talla:
                usar("calcular_grasa_corporal", {"peso_kg": peso, "talla_m": talla, "edad": edad, "sexo": sexo})

        # Tool 3: Formatear salida
        if "tabla" in t:
            usar("formatear_salida", {"datos": "Resumen clínico y antropométrico", "formato": "tabla"})
        elif "xml" in t:
            usar("formatear_salida", {"datos": "Resumen clínico y antropométrico", "formato": "xml"})
        elif "markdown" in t or "documento" in t or "informe" in t:
            usar("formatear_salida", {"datos": "Resumen clínico y antropométrico", "formato": "markdown"})

        # Remisiones
        remitir = re.search(r"rem[ií]t", t)
        if remitir:
            usar("cargar_skill", {"nombre": "remision-especialista"})

        # Consulta de paciente abierto
        if re.search(r"paciente|este caso|sus datos|cama", t):
            usar("consultar_paciente", {})

        # Consulta RAG / guías
        if re.search(r"gu[ií]a|protocolo|qu[eé] hago|recomend|hba1c|retinopat|glucosa|tamizaje|meta|cu[aá]ndo|ventilador|iec|norma|biom[eé]dico|fuga|metrolog", t):
            usar("buscar_guias", {"pregunta": texto[:200]})

        if remitir:
            esp = next((nombre for raiz, nombre in ESPECIALIDADES.items() if raiz in t), "medicina interna")
            usar("proponer_remision", {"especialidad": esp, "motivo": texto[:150]})

        if re.search(r"recuerda que|prefiero", t):
            usar("guardar_preferencia", {"preferencia": texto[:160]})

        return plan

    def _responder(self, mensajes) -> AIMessage:
        humanos = [i for i, m in enumerate(mensajes) if isinstance(m, HumanMessage)]
        if not humanos:
            return AIMessage(content="[simulado] No veo ninguna pregunta del usuario en el contexto.")
        i = humanos[-1]
        pregunta = mensajes[i].content
        resultados = [m for m in mensajes[i + 1:] if isinstance(m, ToolMessage)]
        plan = self._plan(pregunta)
        if len(resultados) < len(plan):
            nombre, args = plan[len(resultados)]
            llamada = {"name": nombre, "args": args, "id": "call_" + uuid.uuid4().hex[:8], "type": "tool_call"}
            return AIMessage(content="", tool_calls=[llamada])
        if not resultados:
            return AIMessage(content=f"[simulado] Recibí: «{pregunta[:90]}». No necesité herramientas.")
        resumen = "\n".join(f"- {m.name}: {str(m.content)[:220]}" for m in resultados)
        return AIMessage(content="[simulado] Respuesta procesada con las herramientas:\n" + resumen)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        time.sleep(self.latencia)
        return ChatResult(generations=[ChatGeneration(message=self._responder(messages))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        await asyncio.sleep(self.latencia)
        return ChatResult(generations=[ChatGeneration(message=self._responder(messages))])
