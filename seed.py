from database import SessionLocal
import models
import bcrypt

db = SessionLocal()

# Comprobamos si ya existe para no duplicarlo
existing_user = db.query(models.Usuario).filter(models.Usuario.email == "ana@pegasia.es").first()

# Generamos el hash de la contraseña de forma directa y segura con bcrypt
password_plain = "T28912peg"
hashed_bytes = bcrypt.hashpw(password_plain.encode('utf-8'), bcrypt.gensalt())
hashed_password_str = hashed_bytes.decode('utf-8')

if not existing_user:
    user = models.Usuario(
        email="ana@pegasia.es",
        hashed_password=hashed_password_str,
        rol="admin"
    )
    db.add(user)
    db.commit()
    print("¡Usuario de prueba creado con éxito!")
else:
    # Si ya existía, le actualizamos la contraseña por si acaso
    existing_user.hashed_password = hashed_password_str
    db.commit()
    print("¡Usuario de prueba actualizado con éxito!")

db.close()