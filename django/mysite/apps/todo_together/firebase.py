# app/firebase.py
import firebase_admin
from firebase_admin import credentials

from django.conf import settings

default_app = None

def get_firebase_app():
    global default_app
    if default_app is None:
        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_FILE)
        default_app = firebase_admin.initialize_app(cred)
    return default_app
