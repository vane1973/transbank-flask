# manage.py

# IMPORTACIÓN LIBRERIAS FLASK PARA GENERACIÓN DE API
from flask import Flask, request, jsonify
# IMPORTACIÓN DE LIBRERIA FLASK PARA DEFINIR POLITICAS DE ACCESO Y COMPARTICIÓN DE RECURSOS REMOTOS AL SERVICIO
from flask_cors import CORS
# IMPORTACIÓN DE LIBREARIA PARA CREAR CLIENTE API REST Y CONSUMIR APIS DE TERCEROS
import requests
# LIBRERÍA PARA SERIALIZAR OBJETOS JSON
#import json

# IMPORTACIONES PARA FIREBASE
import os
import datetime as dt # Aún útil para formatear fechas si es necesario, aunque firestore.SERVER_TIMESTAMP es preferible
from firebase_admin import firestore # Importar firestore directamente
from firebase_config import db # Asume que firebase_config.py define 'db'

import traceback

app = Flask(__name__)
CORS(app)

# SE HABILITA ACCESO PARA API DESDE EL ORIGEN *
cors = CORS(app, resource={
    # RUTA O RUTAS HABILITADAS PARA SER CONSUMIDAS 
    r"/api/v1/transbank/*":{
        "origins":"*"
    }
})

# --- Mejorar la forma de obtener las credenciales de Transbank ---
# Carga las credenciales de Transbank desde variables de entorno
# En un archivo .env (usando python-dotenv, por ejemplo)
# TRANSBANK_API_KEY_ID = "597055555532"
# TRANSBANK_API_KEY_SECRET = "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C"

TRANSBANK_API_KEY_ID = os.getenv('TRANSBANK_API_KEY_ID', '597055555532') # Valor por defecto para pruebas
TRANSBANK_API_KEY_SECRET = os.getenv('TRANSBANK_API_KEY_SECRET', '579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C') # Valor por defecto para pruebas


# MÉTODO QUE CREA LA CABECERA SOLICITADA POR TRANSBANK EN UN REQUEST (SOLICITUD)
def header_request_transbank():
    headers = { # DEFINICIÓN TIPO DE AUTORIZACIÓN Y AUTENTICACIÓN
                "Authorization": "Token",
                "Tbk-Api-Key-Id": TRANSBANK_API_KEY_ID,
                "Tbk-Api-Key-Secret": TRANSBANK_API_KEY_SECRET,
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",
                'Referrer-Policy': 'origin-when-cross-origin',
                } 
    return headers

# DEFINICIÓN DE RUTA API REST, PERMITIENDO SOLO SER LLAMADO POR POST
@app.route('/api/v1/transbank/transaction/create', methods=['POST'])
def transbank_create():
    print('headers: ', request.headers)
    data = request.json
    print('data: ', data)
    
    if not all(k in data for k in ["buy_order", "session_id", "amount", "return_url"]):
        return jsonify({"message": "Datos de transacción incompletos."}), 400

    url = "https://webpay3gint.transbank.cl/rswebpaytransaction/api/webpay/v1.2/transactions"
    headers = header_request_transbank()
    
    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status() # Lanza un HTTPError si la respuesta no es 2xx
        transbank_response = response.json()
        print('response (Transbank Create): ', transbank_response)
        
        # Opcional: Guardar un registro inicial en Firebase Firestore para seguimiento
        try:
            db.collection('transbank_testing').add({
                'buy_order': data.get('buy_order'),
                'amount': data.get('amount'),
                'status': 'PENDING_INITIATION',
                'transbank_token': transbank_response.get('token'),
                'created_at': firestore.SERVER_TIMESTAMP
            })
            print(f"Registro inicial de buy_order {data.get('buy_order')} guardado en Firebase.")
        except Exception as fb_err:
            print(f"ERROR al guardar registro inicial en Firebase: {fb_err}")
            # Este error no debería detener la creación de la transacción Transbank

        return jsonify(transbank_response), 200
    except requests.exceptions.RequestException as e:
        print(f"Error al llamar a Transbank Create: {e}")
        return jsonify({"message": f"Error al comunicarse con Transbank: {str(e)}"}), 500
    except Exception as e:
        print(f"Error inesperado en transbank_create: {e}")
        return jsonify({"message": f"Error interno del servidor: {str(e)}"}), 500

# DEFINICIÓN DE RUTA API REST CON UN PARAMETRO DE ENTRADA (tokenws) EN EL PATH, PERMITIENDO SOLO SER LLAMADO POR GET
@app.route('/api/v1/transbank/transaction/commit/<string:tokenws>', methods=['PUT'])
def transbank_commit(tokenws):
    print('tokenws: ', tokenws)
    url = f"https://webpay3gint.transbank.cl/rswebpaytransaction/api/webpay/v1.2/transactions/{tokenws}"
    print('url: ', url)
    headers = header_request_transbank()
    
    try:
        response = requests.put(url, headers=headers)
        response.raise_for_status() # Lanza un HTTPError si la respuesta no es 2xx
        transbank_response = response.json()
        print('response (Transbank Commit): ', transbank_response)

        # --- INICIO DE LA LÓGICA DE FIREBASE ---
        transaction_data_to_save = {
            'buy_order': transbank_response.get('buy_order'),
            'session_id': transbank_response.get('session_id'),
            'amount': transbank_response.get('amount'),
            'status': transbank_response.get('status'), # Ej: 'AUTHORIZED', 'FAILED'
            'response_code': transbank_response.get('response_code'),
            'vci': transbank_response.get('vci'),
            # Aseguramos que solo guardamos los últimos 4 dígitos de la tarjeta
            'card_number_last_4': transbank_response.get('card_detail', {}).get('card_number', 'N/A')[-4:] if transbank_response.get('card_detail', {}).get('card_number') else 'N/A',
            'accounting_id': transbank_response.get('accounting_id'),
            'transaction_date_tbk': transbank_response.get('transaction_date'), # Fecha de Transbank (string ISO)
            'authorization_code': transbank_response.get('authorization_code'),
            'payment_type_code': transbank_response.get('payment_type_code'),
            'installments_number': transbank_response.get('installments_number'),
            'commerce_code': transbank_response.get('commerce_code'),
            'transbank_token_ws': tokenws, # Guarda el token_ws para referencia
            'processed_at': firestore.SERVER_TIMESTAMP, # Marca de tiempo del servidor al guardar
            # Guarda la respuesta completa de Transbank para un registro detallado
            'raw_transbank_response': transbank_response
        }

        try:
            # Usar el buy_order como ID del documento en Firestore si es único y presente
            buy_order = transbank_response.get('buy_order')
            if buy_order:
                doc_ref = db.collection('transbank_transactions').document(str(buy_order))
                doc_ref.set(transaction_data_to_save)
                print(f"✅ Transacción {buy_order} guardada en Firebase Firestore.")

                # Opcional: Actualizar el registro inicial si lo creaste
                # (Asume que el ID del documento inicial era el buy_order)
                # Si usaste .add(), necesitarías guardar el ID del documento devuelto por .add()
                # y luego usar ese ID para actualizar.
                # Para simplificar, si no estás actualizando, puedes omitir esta parte.
                try:
                    initial_requests_ref = db.collection('transbank_testing').where('buy_order', '==', buy_order).limit(1).get()
                    for doc in initial_requests_ref:
                        doc.reference.update({
                            'status': 'CONFIRMED_' + transbank_response.get('status', 'UNKNOWN'),
                            'response_code': transbank_response.get('response_code'),
                            'confirmed_at': firestore.SERVER_TIMESTAMP
                        })
                        print(f"Estado de solicitud inicial {buy_order} actualizado en Firebase.")
                except Exception as update_err:
                    print(f"ADVERTENCIA: No se pudo actualizar el registro inicial en Firebase para {buy_order}: {update_err}")

            else:
                # Si no hay buy_order, guarda con un ID automático
                db.collection('transbank_transactions').add(transaction_data_to_save)
                print("✅ Transacción guardada en Firebase Firestore con ID automático (sin buy_order).")

        except Exception as fb_error:
            print(f"❌ ERROR al guardar/actualizar transacción en Firebase: {fb_error}")
            # Puedes registrar este error en otra colección de Firebase para monitoreo
            db.collection('transbank_firebase_errors').add({
                'error_type': 'Firestore Save Error',
                'token_ws': tokenws,
                'message': str(fb_error),
                'timestamp': firestore.SERVER_TIMESTAMP,
                'traceback': traceback.format_exc(),
                'transbank_response': transbank_response # Guarda la respuesta completa para depuración
            })
            # Este error no debería detener la respuesta a la app cliente si Transbank ya confirmó.
        # --- FIN DE LA LÓGICA DE FIREBASE ---

        return jsonify(transbank_response), 200

    except requests.exceptions.RequestException as e:
        print(f"Error al llamar a Transbank Commit: {e}")
        # Si Transbank devuelve un error HTTP, devuelve un JSON con el mensaje de error
        error_message = response.text if response.text else str(e)
        return jsonify({"message": f"Error al comunicarse con Transbank: {error_message}"}), response.status_code if response.status_code else 500
    except Exception as e:
        print(f"Error inesperado en transbank_commit: {e}")
        return jsonify({"message": f"Error interno del servidor: {str(e)}"}), 500


# DEFINICIÓN DE RUTA API REST CON UN PARAMETRO DE ENTRADA (tokenws, amount) EN EL PATH, PERMITIENDO SOLO SER LLAMADO POR POST
@app.route('/api/v1/transbank/transaction/reverse-or-cancel/<string:tokenws>', methods=['POST'])
def transbank_reverse_or_cancel(tokenws):
    print('tokenws: ', tokenws)
    data = request.json
    print('data: ', data)
    
    if not data or 'amount' not in data:
        return jsonify({"message": "Monto de reversión/cancelación requerido."}), 400

    url = f"https://webpay3gint.transbank.cl/rswebpaytransaction/api/webpay/v1.2/transactions/{tokenws}/refunds"
    headers = header_request_transbank()
    
    try:
        response = requests.post(url, json=data, headers=headers)
        response.raise_for_status() # Lanza un HTTPError si la respuesta no es 2xx
        transbank_response = response.json()
        print('response (Transbank Refund): ', transbank_response)

        # Opcional: Registrar la reversión/cancelación en Firebase
        try:
            # Buscar la transacción original en Firebase por token_ws o buy_order
            # y actualizar su estado a 'REFUNDED' o 'CANCELLED'
            # Esta parte depende de cómo almacenes tus transacciones
            # Por simplicidad, se puede añadir un nuevo documento de "reembolso"
            db.collection('transbank_refunds').add({
                'original_token_ws': tokenws,
                'refund_amount': data.get('amount'),
                'refund_response': transbank_response,
                'refund_at': firestore.SERVER_TIMESTAMP
            })
            print(f"Reembolso/Anulación para token {tokenws} guardado en Firebase.")
        except Exception as fb_err:
            print(f"ERROR al guardar reembolso en Firebase: {fb_err}")

        return jsonify(transbank_response), 200
    except requests.exceptions.RequestException as e:
        print(f"Error al llamar a Transbank Refund: {e}")
        error_message = response.text if response.text else str(e)
        return jsonify({"message": f"Error al comunicarse con Transbank para reembolso: {error_message}"}), response.status_code if response.status_code else 500
    except Exception as e:
        print(f"Error inesperado en transbank_reverse_or_cancel: {e}")
        return jsonify({"message": f"Error interno del servidor: {str(e)}"}), 500

# DESPLIEGUE SERVICIO PROPIO DE FLASK (SOLO PARA PRUEBAS). EN DONDE AL DEFINI 0.0.0.0 SE 
# HABILITA EL USO DE LA IP LOCAL, IP DE RED, ETC. PARA EL PUERTO 8900
if __name__ == '__main__':
    # Asegúrate de que firebase_config.py inicialice Firebase
    # Esto es vital para que 'db' esté disponible.
    # from firebase_config import initialize_firebase # Si tienes una función de inicialización
    # initialize_firebase() # Llama a la función si existe

    app.run(host='0.0.0.0', port=8900, debug=True)