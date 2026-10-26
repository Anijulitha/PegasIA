from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

class Despacho(Base):
    __tablename__ = "despachos"
    
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    plan = Column(String, default="basico")
    
    # Relaciones
    usuarios = relationship("Usuario", back_populates="despacho")
    expedientes = relationship("Expediente", back_populates="despacho")

class Usuario(Base):
    __tablename__ = "usuarios"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True) # <-- Añade esta línea
    rol = Column(String, default="abogado")
    despacho_id = Column(Integer, ForeignKey("despachos.id"))
    
    despacho = relationship("Despacho", back_populates="usuarios")

class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String, index=True)
    dni = Column(String, index=True) # ¡Asegúrate de que NO tenga unique=True!
    
    # NUEVO: Aislamiento Multi-Tenant
    despacho_id = Column(Integer, ForeignKey("despachos.id"), nullable=True)

    # Relaciones
    despacho = relationship("Despacho")
    expedientes = relationship("Expediente", back_populates="cliente")

class Expediente(Base):
    __tablename__ = "expedientes"
    
    id = Column(Integer, primary_key=True, index=True)
    referencia = Column(String, unique=True, index=True)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    
    despacho_id = Column(Integer, ForeignKey("despachos.id"))
    cliente_id = Column(Integer, ForeignKey("clientes.id"))
    
    despacho = relationship("Despacho", back_populates="expedientes")
    cliente = relationship("Cliente", back_populates="expedientes")
    documentos = relationship("DocumentoGenerado", back_populates="expediente")

class DocumentoGenerado(Base):
    __tablename__ = "documentos_generados"
    
    id = Column(Integer, primary_key=True, index=True)
    tipo_documento = Column(String) # Ej: "Arrendamiento Vivienda"
    ruta_archivo = Column(String)   # Dónde está guardado el .docx o .pdf
    datos_json = Column(JSON)       # Guardamos el payload exacto para poder regenerarlo
    fecha_generacion = Column(DateTime, default=datetime.utcnow)
    
    expediente_id = Column(Integer, ForeignKey("expedientes.id"))
    expediente = relationship("Expediente", back_populates="documentos")
    
class Plantilla(Base):
    __tablename__ = "plantillas"

    id = Column(Integer, primary_key=True, index=True) # ¡Corregido el primary_key!
    nombre = Column(String, index=True)  
    ruta_archivo = Column(String)        
    fecha_subida = Column(DateTime, default=datetime.utcnow)
    
    # Lo vinculamos al despacho para que sea compartido por toda la entidad
    despacho_id = Column(Integer, ForeignKey("despachos.id")) 
    despacho = relationship("Despacho")  