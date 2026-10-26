from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
from docxtpl import DocxTemplate
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
import os
import uuid
import google.generativeai as genai
import zipfile
import io
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta, timezone


from database import get_db
import models
import shutil
from fastapi import UploadFile, File, Form
import re
from docx import Document
from pydantic import BaseModel
from typing import Dict, Any
from fastapi.responses import FileResponse
from docxtpl import DocxTemplate
from dotenv import load_dotenv
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from fastapi.security import OAuth2PasswordBearer
from fastapi import Depends, HTTPException
from database import engine

models.Base.metadata.create_all(bind=engine)

# Cargar variables de entorno antes de nada
load_dotenv()

app = FastAPI(title="PegasIA Legal API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuración del modelo de IA
clave_gemini = os.getenv("GEMINI_API_KEY")
if not clave_gemini:
    print("⚠️ ADVERTENCIA: No se ha encontrado GEMINI_API_KEY en el archivo .env")
genai.configure(api_key=clave_gemini)
modelo_ia = genai.GenerativeModel('gemini-1.5-flash')

# --- CONFIGURACIÓN DE SEGURIDAD (JWT Y BCRYPT) ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# --- CONFIGURACIÓN DE SEGURIDAD (JWT Y BCRYPT NATIVO) ---
ALGORITHM = "HS256"
SECRET_KEY = os.getenv("SECRET_KEY", "super_secreto_temporal")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")

# Este es el nuevo guardián que pondremos en las puertas
async def obtener_usuario_actual(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        usuario_id = payload.get("sub")
        if usuario_id is None:
            raise HTTPException(status_code=401, detail="Token inválido")
        
        usuario = db.query(models.Usuario).filter(models.Usuario.id == int(usuario_id)).first()
        if not usuario:
            raise HTTPException(status_code=401, detail="Usuario no encontrado")
        return usuario
        
    except Exception:
        raise HTTPException(status_code=401, detail="Token expirado o inválido")

def verificar_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password: 
        return False
    # bcrypt nativo requiere que los textos se conviertan a bytes ('utf-8')
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def obtener_password_hash(password: str) -> str:
    # Generamos la "sal" criptográfica y ciframos
    salt = bcrypt.gensalt()
    hashed_bytes = bcrypt.hashpw(password.encode('utf-8'), salt)
    return hashed_bytes.decode('utf-8') # Lo guardamos como texto en PostgreSQL

def crear_token(data: dict):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=7) # El token dura 7 días
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

class LoginRequest(BaseModel):
    email: str
    password: str  # ¡Añadimos la contraseña obligatoria!

@app.post("/api/login")
async def login(req: LoginRequest, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.email == req.email).first()
    
    # Comprobamos que el usuario existe y que la contraseña coincide matemáticamente
    if not usuario or not verificar_password(req.password, usuario.hashed_password):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
        
    # Generamos el Token JWT firmado con el SECRET_KEY de tu .env
    token = crear_token({"sub": str(usuario.id)})
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario_id": usuario.id,
        "despacho_nombre": usuario.despacho.nombre
    }

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


@app.post("/api/plantillas/subir")
async def subir_plantilla_segura(
    nombre: str = Form(...),
    archivo: UploadFile = File(...),
    usuario: models.Usuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db)
):
    if not usuario:
        raise HTTPException(status_code=403, detail="Acceso denegado")
        
    # 2. Validación de extensión estricta
    if not archivo.filename.lower().endswith('.docx'):
        raise HTTPException(status_code=400, detail="Extensión no permitida. Solo .docx")
    
    # 3. Validación de contenido real (Un .docx es estructuralmente un ZIP)
    contenido = await archivo.read()
    try:
        with zipfile.ZipFile(io.BytesIO(contenido)) as zf:
            # Comprobamos que tiene la estructura interna de un Word
            if '[Content_Types].xml' not in zf.namelist():
                raise ValueError("No es un Word válido")
    except Exception:
        raise HTTPException(status_code=400, detail="El archivo está corrupto o no es un documento Word real")
        
    # Restablecemos el cursor del archivo después de leerlo
    await archivo.seek(0)
        
    # 4. Prevención de Path Traversal y sobreescrituras (UUID)
    carpeta_despacho = f"templates/despacho_{usuario.despacho_id}"
    os.makedirs(carpeta_despacho, exist_ok=True)
    
    # Generamos un nombre único irrompible ignorando el nombre original
    nombre_seguro = f"{uuid.uuid4()}.docx"
    ruta_completa = os.path.join(carpeta_despacho, nombre_seguro)
    
    # 5. Guardado físico
    with open(ruta_completa, "wb") as buffer:
        buffer.write(contenido)
        
    # 6. Registro en Base de Datos (Guardamos el nombre original solo para mostrarlo en la web)
    nueva_plantilla = models.Plantilla(
        nombre=nombre,
        ruta_archivo=ruta_completa,
        despacho_id=usuario.despacho_id
    )
    db.add(nueva_plantilla)
    db.commit()
    db.refresh(nueva_plantilla)
    
    return {
        "mensaje": "Plantilla verificada y subida de forma segura", 
        "plantilla_id": nueva_plantilla.id
    } 

# --- ENDPOINTS: GENERACIÓN ---
@app.post("/api/generar/confidencialidad")
async def generar_confidencialidad(
    datos: ContratoConfidencialidad, 
    usuario: models.Usuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db)
):

    ruta_plantilla = "templates/plantilla_confidencialidad.docx"
    nombre_limpio = datos.empresa_receptora.replace(" ", "_")
    ruta_salida = f"output/nda_{nombre_limpio}.docx"
    
    if not os.path.exists(ruta_plantilla):
        raise HTTPException(status_code=500, detail="Plantilla NDA no encontrada.")

    doc = DocxTemplate(ruta_plantilla)
    doc.render(datos.model_dump())
    doc.save(ruta_salida)

    # 1. Cliente (Aislado por Despacho)
    cliente = db.query(models.Cliente).filter(
        models.Cliente.nombre_completo == datos.empresa_receptora,
        models.Cliente.despacho_id == usuario.despacho_id  # <-- Filtro Multi-Tenant
    ).first()
    
    if not cliente:
        cliente = models.Cliente(
            nombre_completo=datos.empresa_receptora, 
            dni="N/A",
            despacho_id=usuario.despacho_id  # <-- Asignación Multi-Tenant
        )
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
    usuario: models.Usuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db)
):
    ruta_plantilla = "templates/plantilla_arrendamiento.docx"
    ruta_salida = f"output/contrato_{datos.arrendador.dni}.docx"
    
    if not os.path.exists(ruta_plantilla):
        raise HTTPException(status_code=500, detail="Plantilla no encontrada.")

    doc = DocxTemplate(ruta_plantilla)
    doc.render(datos.model_dump())
    doc.save(ruta_salida)

    # 1. Cliente (Aislado por Despacho)
    cliente = db.query(models.Cliente).filter(
        models.Cliente.dni == datos.arrendador.dni,
        models.Cliente.despacho_id == usuario.despacho_id  # <-- Filtro Multi-Tenant
    ).first()
    
    if not cliente:
        cliente = models.Cliente(
            nombre_completo=datos.arrendador.nombre, 
            dni=datos.arrendador.dni,
            despacho_id=usuario.despacho_id  # <-- Asignación Multi-Tenant
        )
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
async def listar_documentos(
    usuario: models.Usuario = Depends(obtener_usuario_actual), 
    db: Session = Depends(get_db)
):
    documentos = db.query(models.DocumentoGenerado)\
        .join(models.Expediente)\
        .filter(models.Expediente.despacho_id == usuario.despacho_id)\
        .order_by(models.DocumentoGenerado.fecha_generacion.desc()).all()
    
    resultado = []
    for doc in documentos:
        # Leemos el nombre directamente de la ficha del cliente vinculada al expediente
        if doc.expediente and doc.expediente.cliente:
            nombre_cliente = doc.expediente.cliente.nombre_completo
        else:
            nombre_cliente = "Desconocido"

        resultado.append({
            "id": doc.id,
            "tipo": doc.tipo_documento,
            "fecha": doc.fecha_generacion.strftime("%d/%m/%Y %H:%M"),
            "cliente": nombre_cliente
        })
        
    return resultado

@app.get("/api/documentos/{doc_id}/descargar")
async def descargar_documento(
    doc_id: int, 
    usuario: models.Usuario = Depends(obtener_usuario_actual), 
    db: Session = Depends(get_db)
):
    
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
    
@app.get("/api/plantillas")
async def listar_plantillas(
    usuario: models.Usuario = Depends(obtener_usuario_actual), 
    db: Session = Depends(get_db)
):
    plantillas = db.query(models.Plantilla).filter(
        models.Plantilla.despacho_id == usuario.despacho_id
    ).all()
    
    return plantillas   

@app.get("/api/plantillas/{plantilla_id}/campos")
async def obtener_campos_plantilla(
    plantilla_id: int, 
    usuario: models.Usuario = Depends(obtener_usuario_actual), 
    db: Session = Depends(get_db)
):
    # 1. Buscamos la plantilla asegurando que pertenece al despacho del usuario
    plantilla = db.query(models.Plantilla).filter(
        models.Plantilla.id == plantilla_id,
        models.Plantilla.despacho_id == usuario.despacho_id
    ).first()
    
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
    try:
        doc = Document(plantilla.ruta_archivo)
        textos = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    textos.extend([p.text for p in cell.paragraphs])
                    
        texto_completo = " ".join(textos)
        
        # 1. Detectar variables normales: {{ nombre_cliente }}
        variables_texto = set(re.findall(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}', texto_completo))
        
        # 2. Detectar booleanos en condicionales: {% if hay_fiador %} o {%p if hay_fiador %}
        variables_booleanas = set(re.findall(r'\{%[\sp]*if\s+([a-zA-Z0-9_]+)[\sp]*%\}', texto_completo))
        
        # 3. Detectar listas en bucles: {% for anexo in lista_anexos %}
        variables_lista = set(re.findall(r'\{%[\sp]*for\s+[a-zA-Z0-9_]+\s+in\s+([a-zA-Z0-9_]+)[\sp]*%\}', texto_completo))
        
        # Limpieza: Si una variable se usa en un 'if' o 'for', la quitamos de las normales
        variables_texto = variables_texto - variables_booleanas - variables_lista
        
        # 4. Estructurar la respuesta
        campos_clasificados = []
        for v in variables_booleanas:
            campos_clasificados.append({"nombre": v, "tipo": "boolean"})
        for v in variables_lista:
            campos_clasificados.append({"nombre": v, "tipo": "list"})
        for v in variables_texto:
            campos_clasificados.append({"nombre": v, "tipo": "text"})
            
        return {"campos": campos_clasificados}
        
    except Exception as e:
        print(f"💥 ERROR LEYENDO EL WORD: {e}")
        raise HTTPException(status_code=500, detail="Error interno analizando el Word")
    
# Estructura para recibir el diccionario de datos variables
class DatosCustom(BaseModel):
    datos: Dict[str, Any]

@app.post("/api/generar/custom/{plantilla_id}")
async def generar_documento_custom(
    plantilla_id: int, 
    payload: DatosCustom,
    usuario: models.Usuario = Depends(obtener_usuario_actual), 
    db: Session = Depends(get_db)
):
    # 1. Buscamos la plantilla asegurando que es del despacho
    plantilla = db.query(models.Plantilla).filter(
        models.Plantilla.id == plantilla_id,
        models.Plantilla.despacho_id == usuario.despacho_id
    ).first()
    
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
    try:
        # 2. Abrimos el Word, inyectamos los datos y lo guardamos con nombre único
        doc = DocxTemplate(plantilla.ruta_archivo)
        doc.render(payload.datos)
        
        carpeta_salida = f"templates/despacho_{usuario.despacho_id}/generados"
        os.makedirs(carpeta_salida, exist_ok=True)
        
        codigo_unico = uuid.uuid4().hex[:6].upper()
        nombre_limpio = plantilla.nombre.replace(" ", "_")
        nombre_archivo = f"Generado_{nombre_limpio}_{codigo_unico}.docx"
        ruta_salida = f"{carpeta_salida}/{nombre_archivo}"
        doc.save(ruta_salida)
        
        # 3. Intentar detectar el nombre del cliente entre los campos dinámicos
        nombre_cliente = "Cliente General"
        dni_cliente = "N/A"
        for clave, valor in payload.datos.items():
            if isinstance(valor, str) and valor.strip():
                clave_lower = clave.lower()
                if any(palabra in clave_lower for palabra in ["nombre", "cliente", "empresa", "arrendador", "arrendatario", "parte"]):
                    nombre_cliente = valor.strip()
                    break
        
        # 4. Cliente (Aislado por Despacho)
        cliente = db.query(models.Cliente).filter(
            models.Cliente.nombre_completo == nombre_cliente,
            models.Cliente.despacho_id == usuario.despacho_id
        ).first()
        
        if not cliente:
            cliente = models.Cliente(
                nombre_completo=nombre_cliente,
                dni=dni_cliente,
                despacho_id=usuario.despacho_id
            )
            db.add(cliente)
            db.commit()
            db.refresh(cliente)
            
        # 5. Expediente (Vinculado al Despacho y Cliente)
        expediente = models.Expediente(
            referencia=f"EXP-{codigo_unico}",
            despacho_id=usuario.despacho_id,
            cliente_id=cliente.id
        )
        db.add(expediente)
        db.commit()
        db.refresh(expediente)
        
        # 6. Documento Generado (Para el Historial)
        nuevo_doc = models.DocumentoGenerado(
            tipo_documento=f"Plantilla: {plantilla.nombre}",
            ruta_archivo=ruta_salida,
            datos_json=payload.datos,
            expediente_id=expediente.id
        )
        db.add(nuevo_doc)
        db.commit()
        
        # 7. Devolvemos el archivo final al navegador
        return FileResponse(
            path=ruta_salida, 
            filename=nombre_archivo, 
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    except Exception as e:
        print(f"💥 ERROR AL GENERAR: {e}")
        raise HTTPException(status_code=500, detail="Error generando el documento")  
    
class RegistroTemporal(BaseModel):
    email: str
    password: str
    despacho_id: int

@app.post("/api/setup_admin")
async def setup_admin(datos: RegistroTemporal, db: Session = Depends(get_db)):
    usuario = db.query(models.Usuario).filter(models.Usuario.email == datos.email).first()
    if usuario:
        # Si ya existe (ej: ana@pegasia.es), le ciframos y guardamos la nueva contraseña
        usuario.hashed_password = obtener_password_hash(datos.password)
        db.commit()
        return {"mensaje": f"Contraseña actualizada y cifrada para {datos.email}"}
    
    # Si no existe, lo crea de cero
    nuevo_usuario = models.Usuario(
        email=datos.email,
        hashed_password=obtener_password_hash(datos.password),
        despacho_id=datos.despacho_id,
        rol="admin"
    )
    db.add(nuevo_usuario)
    db.commit()
    return {"mensaje": "Usuario creado con contraseña cifrada"} 

@app.post("/api/ia/redactar")
async def redactar_texto_juridico(
    peticion: PeticionIA, 
    usuario: models.Usuario = Depends(obtener_usuario_actual)
):
    try:
        # Le damos un "rol" estricto a la IA para que actúe como abogado
        prompt = f"""
        Eres un abogado experto en derecho mercantil y civil en España. 
        Tu tarea es expandir y convertir la siguiente instrucción breve en un texto jurídico formal, preciso, elegante y blindado legalmente.
        El texto resultante se insertará directamente en un contrato formal (como el propósito de un NDA o una cláusula).
        
        REGLAS:
        - No incluyas saludos, despedidas ni introducciones (nada de "Aquí tienes la cláusula:").
        - No uses comillas al principio ni al final.
        - Escribe en tercera persona y con terminología jurídica adecuada.
        
        Instrucción original del cliente: "{peticion.texto_breve}"
        """
        
        # Invocamos al modelo
        respuesta = modelo_ia.generate_content(prompt)
        texto_limpio = respuesta.text.strip()
        
        return {"texto_generado": texto_limpio}
        
    except Exception as e:
        print(f"💥 ERROR DE IA: {e}")
        raise HTTPException(status_code=500, detail="Error generando el texto jurídico con IA")   
    
@app.get("/api/clientes")
async def listar_clientes(
    usuario: models.Usuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db)
):
    # Devuelve únicamente los clientes del despacho del usuario logueado
    clientes = db.query(models.Cliente).filter(
        models.Cliente.despacho_id == usuario.despacho_id
    ).all()
    return clientes

@app.delete("/api/plantillas/{plantilla_id}")
async def eliminar_plantilla(
    plantilla_id: int,
    usuario: models.Usuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db)
):
    plantilla = db.query(models.Plantilla).filter(
        models.Plantilla.id == plantilla_id,
        models.Plantilla.despacho_id == usuario.despacho_id
    ).first()
    
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
    # Borramos el archivo físico del servidor si existe
    if os.path.exists(plantilla.ruta_archivo):
        try:
            os.remove(plantilla.ruta_archivo)
        except Exception:
            pass
            
    db.delete(plantilla)
    db.commit()
    return {"mensaje": "Plantilla eliminada correctamente"}

class PlantillaUpdate(BaseModel):
    nombre: str

@app.put("/api/plantillas/{plantilla_id}")
async def actualizar_plantilla(
    plantilla_id: int,
    payload: PlantillaUpdate,
    usuario: models.Usuario = Depends(obtener_usuario_actual),
    db: Session = Depends(get_db)
):
    plantilla = db.query(models.Plantilla).filter(
        models.Plantilla.id == plantilla_id,
        models.Plantilla.despacho_id == usuario.despacho_id
    ).first()
    
    if not plantilla:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
        
    plantilla.nombre = payload.nombre
    db.commit()
    return {"mensaje": "Plantilla actualizada con éxito"}    
    