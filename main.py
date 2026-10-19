from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from docxtpl import DocxTemplate
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import os
import uuid
import google.generativeai as genai

from database import get_db
import models

app = FastAPI(title="PegasIA Legal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración del modelo de IA (Sustituye por tu clave real)
genai.configure(api_key="TU_CLAVE_API_AQUI")
modelo_ia = genai.GenerativeModel('gemini-1.5-flash')

# --- MODELOS ---
class LoginRequest(BaseModel):
    email: str

class Persona(BaseModel):
    nombre: str
    dni: str

class ContratoArrendamiento(BaseModel):
    ciudad: str
    fecha_larga: str
    arrendador: Persona
    hay_fiador: bool
    fiador: Persona | None = None
    anexos: list[str] = []
    
class ContratoConfidencialidad(BaseModel):
    ciudad: str
    fecha_larga: str
    empresa_emisora: str
    empresa_receptora: str
    proposito: str
    
class PeticionIA(BaseModel):
    texto_breve: str

@app.post("/api/ia/redactar")
async def redactar_proposito_ia(
    peticion: PeticionIA, 
    usuario_id: str = Header(...),
    db: Session = Depends(get_db)  # <-- ¡AQUÍ ESTÁ LA CLAVE QUE FALTABA!
):
    # Verificamos que el usuario tenga sesión iniciada
    usuario = db.query(models.Usuario).filter(models.Usuario.id == int(usuario_id)).first()
    if not usuario:
        raise HTTPException(status_code=401, detail="No autorizado")

    # Prompt engineering para forzar un tono estrictamente jurídico
    prompt = f"""
    Actúa como un abogado corporativo experto. Reescribe este breve propósito para incluirlo en la cláusula de un Acuerdo de Confidencialidad (NDA). 
    Usa lenguaje jurídico formal, preciso y delimitado. Devuelve únicamente el texto redactado, sin comillas ni introducciones. Máximo 3 o 4 líneas.
    Texto original del cliente: {peticion.texto_breve}
    """
    
    try:
        respuesta = modelo_ia.generate_content(prompt)
        return {"texto_generado": respuesta.text.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))   

# --- ENDPOINT: LOGIN / REGISTRO AUTOMÁTICO ---
@app.post("/api/login")
async def iniciar_sesion(datos: LoginRequest, db: Session = Depends(get_db)):
    # Buscamos al usuario
    usuario = db.query(models.Usuario).filter(models.Usuario.email == datos.email).first()
    
    # Si no existe, lo creamos junto con su despacho (Modo MVP)
    if not usuario:
        nombre_despacho = "Despacho " + datos.email.split('@')[0].capitalize()
        nuevo_despacho = models.Despacho(nombre=nombre_despacho)
        db.add(nuevo_despacho)
        db.commit()
        db.refresh(nuevo_despacho)

        usuario = models.Usuario(email=datos.email, despacho_id=nuevo_despacho.id)
        db.add(usuario)
        db.commit()
        db.refresh(usuario)

    return {
        "usuario_id": usuario.id, 
        "email": usuario.email, 
        "despacho_id": usuario.despacho_id,
        "despacho_nombre": usuario.despacho.nombre
    }
    

# --- ENDPOINTS: GENERACIÓN ---
@app.post("/api/generar/confidencialidad")
async def generar_confidencialidad(
    datos: ContratoConfidencialidad, 
    usuario_id: str = Header(...), # EXIGIMOS EL USUARIO
    db: Session = Depends(get_db)
):
    usuario = db.query(models.Usuario).filter(models.Usuario.id == int(usuario_id)).first()
    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no autorizado")

    ruta_plantilla = "templates/plantilla_confidencialidad.docx"
    nombre_limpio = datos.empresa_receptora.replace(" ", "_")
    ruta_salida = f"output/nda_{nombre_limpio}.docx"
    
    if not os.path.exists(ruta_plantilla):
        raise HTTPException(status_code=500, detail="Plantilla NDA no encontrada.")

    doc = DocxTemplate(ruta_plantilla)
    doc.render(datos.model_dump())
    doc.save(ruta_salida)

    # 1. Cliente
    cliente = db.query(models.Cliente).filter(models.Cliente.nombre_completo == datos.empresa_receptora).first()
    if not cliente:
        cliente = models.Cliente(nombre_completo=datos.empresa_receptora, dni="N/A")
        db.add(cliente)
        db.commit()
        db.refresh(cliente)

    # 2. Expediente (Vinculamos Cliente con Despacho)
    expediente = models.Expediente(
        referencia=f"EXP-{uuid.uuid4().hex[:6].upper()}",
        despacho_id=usuario.despacho_id,
        cliente_id=cliente.id
    )
    db.add(expediente)
    db.commit()
    db.refresh(expediente)

    # 3. Documento (Vinculado al Expediente)
    nuevo_doc = models.DocumentoGenerado(
        tipo_documento="Acuerdo de Confidencialidad (NDA)",
        ruta_archivo=ruta_salida,
        datos_json=datos.model_dump(),
        expediente_id=expediente.id
    )
    db.add(nuevo_doc)
    db.commit()

    return FileResponse(ruta_salida, filename=f"NDA_{nombre_limpio}.docx")    

@app.post("/api/generar/arrendamiento")
async def generar_arrendamiento(
    datos: ContratoArrendamiento, 
    usuario_id: str = Header(...), # EXIGIMOS EL USUARIO
    db: Session = Depends(get_db)
):
    usuario = db.query(models.Usuario).filter(models.Usuario.id == int(usuario_id)).first()
    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no autorizado")

    ruta_plantilla = "templates/plantilla_arrendamiento.docx"
    ruta_salida = f"output/contrato_{datos.arrendador.dni}.docx"
    
    if not os.path.exists(ruta_plantilla):
        raise HTTPException(status_code=500, detail="Plantilla no encontrada.")

    doc = DocxTemplate(ruta_plantilla)
    doc.render(datos.model_dump())
    doc.save(ruta_salida)

    # 1. Cliente
    cliente = db.query(models.Cliente).filter(models.Cliente.dni == datos.arrendador.dni).first()
    if not cliente:
        cliente = models.Cliente(nombre_completo=datos.arrendador.nombre, dni=datos.arrendador.dni)
        db.add(cliente)
        db.commit()
        db.refresh(cliente)
        
    # 2. Expediente
    expediente = models.Expediente(
        referencia=f"EXP-{uuid.uuid4().hex[:6].upper()}",
        despacho_id=usuario.despacho_id,
        cliente_id=cliente.id
    )
    db.add(expediente)
    db.commit()
    db.refresh(expediente)

    # 3. Documento
    nuevo_doc = models.DocumentoGenerado(
        tipo_documento="Contrato de Arrendamiento",
        ruta_archivo=ruta_salida,
        datos_json=datos.model_dump(),
        expediente_id=expediente.id
    )
    db.add(nuevo_doc)
    db.commit()

    return FileResponse(ruta_salida, filename="Contrato_Arrendamiento.docx")
    
# --- ENDPOINTS: CONSULTAS ---
@app.get("/api/documentos")
async def listar_documentos(usuario_id: str = Header(...), db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.id == int(usuario_id)).first()
    
    # FILTRO B2B: Solo documentos unidos a un expediente que pertenezca a mi despacho
    documentos = db.query(models.DocumentoGenerado)\
        .join(models.Expediente)\
        .filter(models.Expediente.despacho_id == usuario.despacho_id)\
        .order_by(models.DocumentoGenerado.fecha_generacion.desc()).all()
    
    resultado = []
    for doc in documentos:
        if doc.tipo_documento == "Contrato de Arrendamiento":
            nombre_cliente = doc.datos_json.get("arrendador", {}).get("nombre", "Desconocido")
        else:
            nombre_cliente = doc.datos_json.get("empresa_receptora", "Desconocido")

        resultado.append({
            "id": doc.id,
            "tipo": doc.tipo_documento,
            "fecha": doc.fecha_generacion.strftime("%d/%m/%Y %H:%M"),
            "cliente": nombre_cliente
        })
        
    return resultado    

@app.get("/api/documentos/{doc_id}/descargar")
async def descargar_documento(doc_id: int, usuario_id: str = Header(...), db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.id == int(usuario_id)).first()
    
    # Doble seguridad: que el documento exista y que pertenezca a mi despacho
    documento = db.query(models.DocumentoGenerado)\
        .join(models.Expediente)\
        .filter(
            models.DocumentoGenerado.id == doc_id,
            models.Expediente.despacho_id == usuario.despacho_id
        ).first()
    
    if not documento:
        raise HTTPException(status_code=404, detail="No encontrado o acceso denegado")
        
    if not os.path.exists(documento.ruta_archivo):
        raise HTTPException(status_code=404, detail="El archivo físico ya no existe.")
        
    return FileResponse(documento.ruta_archivo, filename=os.path.basename(documento.ruta_archivo))