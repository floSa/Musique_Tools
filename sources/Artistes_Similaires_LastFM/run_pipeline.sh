#!/bin/bash
# Pipeline complet : vérification → rescue → export
set -e
cd "$(dirname "$0")"

echo "=== Etape 1: Vérification des artistes manquants ==="
python check_missing.py

echo "=== Etape 2: Rescue des artistes manquants ==="
python rescue_missing.py

echo "=== Etape 3: Export vers CSV ==="
python export_to_csv.py

echo "=== Pipeline terminé ==="
