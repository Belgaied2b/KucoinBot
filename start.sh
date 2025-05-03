#!/bin/bash

echo "🚀 Installation des dépendances..."
pip install --upgrade pip
pip install -r requirements.txt

echo "✅ Lancement du bot..."
python3 main.py
