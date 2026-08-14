#!/usr/bin/env python3
"""Lista los parametros disponibles en Catapult para el grupo SEVEN.
Sirve para encontrar el slug exacto de las metricas que usa el PF en su Excel
(ej: "dt + 25 km/h", "sprint sevens esf", "aceleraciones b2-3. total ftto").
Se corre a mano desde Actions; solo imprime, no toca carga.json.
"""
import json, os, sys, urllib.request

TOKEN = os.environ.get("CATAPULT_TOKEN")
if not TOKEN:
    print("ERROR: falta CATAPULT_TOKEN"); sys.exit(1)

BASE = "https://connect-us.catapultsports.com/api/v4"


def get(path):
    req = urllib.request.Request(BASE + path, headers={
        "Authorization": "Bearer " + TOKEN, "Accept": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    for path in ["/parameters", "/parameters?include=all", "/customers/parameters"]:
        try:
            data = get(path)
        except Exception as e:
            print("  %s -> %s" % (path, e))
            continue
        if isinstance(data, dict):
            data = data.get("data") or data.get("parameters") or []
        if not isinstance(data, list) or not data:
            print("  %s -> sin resultados" % path)
            continue
        print("")
        print("=== %d parametros desde %s ===" % (len(data), path))
        print("%-46s | %s" % ("SLUG", "NOMBRE"))
        print("-" * 100)
        for p in sorted(data, key=lambda x: str(x.get("name", "")).lower()):
            slug = p.get("slug") or p.get("parameter") or p.get("id") or ""
            nombre = p.get("name") or p.get("display_name") or ""
            unidad = p.get("unit") or p.get("units") or ""
            print("%-46s | %s%s" % (slug, nombre, ("  [" + str(unidad) + "]") if unidad else ""))
        return
    print("No pude listar parametros por ninguna ruta.")


if __name__ == "__main__":
    main()

