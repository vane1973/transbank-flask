import firebase_admin
from firebase_admin import credentials, firestore

# Ruta al archivo de la clave de servicio
# Asegúrate de que esta ruta sea correcta desde donde se ejecuta la app Flask
SERVICE_ACCOUNT_KEY_PATH = "firebase-key.json"

try:
    # Inicializar Firebase
    cred = credentials.Certificate(SERVICE_ACCOUNT_KEY_PATH)
    firebase_admin.initialize_app(cred)
    print("Firebase inicializado correctamente.")
except Exception as e:
    print(f"Error al inicializar Firebase: {e}")
    # Considera una forma más robusta de manejar este error en producción,
    # como registrar el error y salir si es crítico.

# Cliente Firestore
db = firestore.client()