"""
bajar_sheets.py — Lee el objetivo semanal de GPS de la hoja del PF
("Temporada 26-27 Pumas Seven") y genera plan.json para el módulo RTP.

Solo LEE la hoja (cuenta de servicio con permiso de Lector). Nunca escribe.

Qué toma de cada pestaña "Sem N":
  - El bloque OBJETIVO SEMANAL GPS por día: SEMANA | DIA | PUESTOS |
    DISTANCIA | HSR | DS | ACEL | DECEL. Se ubica por los encabezados,
    no por posición fija, así que si el PF mueve el bloque se sigue leyendo.
  - La fila del calendario (lunes a domingo) para saber a qué día de la
    semana corresponde cada DIA del bloque: los días con "campo" o "CR"
    en orden son DIA 1, 2, 3...; la fila PARTIDOS va al primer día de partido.

Número de semana: el del nombre de la pestaña ("Sem 12 Cba" -> 12). En
pestañas con rango ("Sem 1-4") se usa la columna SEMANA del bloque.
Si dos pestañas son la misma semana, se queda la indicada en ELEGIR o,
si no está, la última de la hoja, y se avisa.

Formato de salida (lo que lee rtp/index.html):
  {"generated": ..., "fuente": "sheets", "avisos": [...],
   "semanas": {"11": {"pestana": "Sem 11",
                      "dias": {"1": "lunes", "2": "martes", ...},
                      "objetivo": {"1": {"FWDS": {"distancia":..., "hsr":..., "ds":..., "accel":..., "deccel":...},
                                         "BACKS": {...}}, ...}}},
   "plantel": {}}

Si algo no cuadra, el script lo avisa en "avisos" y en el log del workflow
en vez de inventar datos.
"""
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

SPREADSHEET_ID = "1jV5LjeTGaltPHsnH48LSr6xOPxloiv_XFhciBC9C_3k"
SALIDA = "plan.json"
RANGO_LECTURA = "A1:J120"

# Si dos pestañas tienen el mismo número de semana, cuál usar: {semana: "nombre exacto de pestaña"}
ELEGIR = {}

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
COLUMNAS = {
    "semana": ["SEMANA"],
    "dia": ["DIA"],
    "puesto": ["PUESTOS", "PUESTO"],
    "distancia": ["DISTANCIA"],
    "hsr": ["HSR"],
    "ds": ["DS"],
    "accel": ["ACEL", "ACC", "ACCEL"],
    "deccel": ["DECEL", "DESAC", "DEC", "DECCEL"],
}
VARS = ["distancia", "hsr", "ds", "accel", "deccel"]


def norm(v):
    s = unicodedata.normalize("NFD", str(v if v is not None else ""))
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def num(v):
    """Número, o rango "350-400" / "350 - 400" como [350, 400]; None si vacío."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else float(v)
    s = str(v).strip().replace(".", "").replace(",", ".")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*[-–a]\s*(\d+(?:\.\d+)?)", s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return [int(a) if a.is_integer() else a, int(b) if b.is_integer() else b]
    try:
        x = float(s)
        return int(x) if x.is_integer() else x
    except ValueError:
        return None


def celda(grid, r, c):
    if 0 <= r < len(grid) and 0 <= c < len(grid[r]):
        return grid[r][c]
    return None


def semana_de_pestana(nombre):
    """'Sem 12 Cba' -> 12 ; 'Sem 1-4' -> None (rango) ; otra cosa -> False."""
    m = re.match(r"\s*sem(?:ana)?\s*(\d+)\s*(-\s*\d+)?", nombre, re.I)
    if not m:
        return False
    return None if m.group(2) else int(m.group(1))


def calendario(grid):
    """Busca la fila con lunes..domingo y devuelve el texto de la fila de abajo por día."""
    for r in range(min(len(grid), 20)):
        fila = [norm(celda(grid, r, c)) for c in range(12)]
        if "lunes" in fila and "domingo" in fila:
            c0 = fila.index("lunes")
            debajo = [norm(celda(grid, r + 1, c0 + i)) for i in range(7)]
            return dict(zip(DIAS, debajo))
    return None


def dias_de_campo(cal):
    """Devuelve (días de campo, días de partido, días activos en orden)."""
    campo, partido, activos = [], [], []
    for d in DIAS:
        t = cal.get(d, "")
        # "campo + gym (sem 9)": ese día es de la semana siguiente, no cuenta acá
        if re.search(r"\(\s*sem", t):
            continue
        if "partido" in t:
            partido.append(d)
            activos.append(d)
        elif "campo" in t or re.fullmatch(r"cr\b.*", t) or t == "cr":
            campo.append(d)
            activos.append(d)
    return campo, partido, activos


def encabezados(grid):
    """Filas de encabezado de bloques DIARIOS (tienen DIA y PUESTOS)."""
    out = []
    for r, fila in enumerate(grid):
        vals = [norm(x).upper() for x in fila]
        if "PUESTOS" in vals and "DIA" in vals and "DISTANCIA" in vals:
            cols = {}
            for k, alias in COLUMNAS.items():
                for i, v in enumerate(vals):
                    if v in alias:
                        cols[k] = i
                        break
            out.append((r, cols))
    return out


def leer_bloque(grid, r0, cols, avisos, pestana):
    """Devuelve {semana_col: {dia_key: {puesto: {var: valor}}}} leyendo hasta TOTAL/fila vacía larga."""
    res = {}
    sem = dia = None
    vacias = 0
    for r in range(r0 + 1, len(grid)):
        p = norm(celda(grid, r, cols["puesto"])).upper()
        s = celda(grid, r, cols["semana"]) if "semana" in cols else None
        d = celda(grid, r, cols["dia"])
        if norm(d).upper() == "SEMANA" or norm(s).upper() == "SEMANA":
            break  # empieza otro bloque
        if not p and d in (None, "") and s in (None, ""):
            vacias += 1
            if vacias > 3:
                break
            continue
        vacias = 0
        if s not in (None, "") and num(s) is not None and not isinstance(num(s), list):
            sem = int(num(s))
        if d not in (None, ""):
            dn = norm(d)
            if dn.startswith("total"):
                dia = "TOTAL"
            elif dn.startswith("partido"):
                dia = "P"
            elif num(d) is not None and not isinstance(num(d), list):
                dia = str(int(num(d)))
            else:
                avisos.append(f"{pestana}: DIA '{d}' no reconocido (fila {r + 1}), se omite.")
                dia = None
        if dia in (None, "TOTAL") or p not in ("FWDS", "BACKS"):
            continue
        valores = {}
        for v in VARS:
            if v not in cols:
                continue
            x = num(celda(grid, r, cols[v]))
            if isinstance(x, list) and x[1] < x[0]:
                avisos.append(f"{pestana}: rango '{celda(grid, r, cols[v])}' en {v.upper()} (fila {r + 1}, {p}) no es válido; queda sin objetivo esa variable.")
                x = None
            valores[v] = x
        res.setdefault(sem, {}).setdefault(dia, {})[p] = valores
    return res


def parsear(pestanas):
    """pestanas: lista de (nombre, grid). Devuelve (semanas, avisos)."""
    semanas, origen, avisos = {}, {}, []
    for nombre, grid in pestanas:
        n_tab = semana_de_pestana(nombre)
        if n_tab is False:
            continue
        cal = calendario(grid)
        heads = encabezados(grid)
        if not heads:
            avisos.append(f"{nombre.strip()}: no tiene bloque de objetivo por día (DIA/PUESTOS), se omite.")
            continue
        campo, partido, activos = dias_de_campo(cal) if cal else ([], [], [])
        for r0, cols in heads:
            if "dia" not in cols or "puesto" not in cols:
                continue
            for sem_col, dias in leer_bloque(grid, r0, cols, avisos, nombre.strip()).items():
                n = n_tab if n_tab is not None else sem_col
                if n is None:
                    avisos.append(f"{nombre.strip()}: bloque sin número de semana, se omite.")
                    continue
                if n_tab is not None and sem_col is not None and sem_col != n_tab:
                    avisos.append(f"{nombre.strip()}: la columna SEMANA dice {sem_col} pero la pestaña es la semana {n_tab}; uso {n_tab}.")
                numericos = sorted((k for k in dias if k != "P"), key=int)
                mapa = {}
                if "P" not in dias and len(numericos) > len(campo) and len(numericos) == len(activos):
                    # semana con partidos dentro del bloque por día (ej. "gym + partidos")
                    for i, k in enumerate(numericos):
                        mapa[k] = activos[i]
                elif len(numericos) <= len(campo):
                    for i, k in enumerate(numericos):
                        mapa[k] = campo[i]
                    if len(numericos) < len(campo):
                        avisos.append(f"Semana {n} ({nombre.strip()}): el calendario tiene {len(campo)} días de campo y el objetivo {len(numericos)}; se asignan en orden ({', '.join(f'D{k}={mapa[k]}' for k in numericos)}).")
                else:
                    std = ["lunes", "martes", "jueves", "viernes", "miercoles", "sabado", "domingo"]
                    for i, k in enumerate(numericos):
                        mapa[k] = std[i]
                    avisos.append(f"Semana {n} ({nombre.strip()}): el objetivo tiene {len(numericos)} días y el calendario {len(campo)} de campo; uso lunes, martes, jueves, viernes.")
                if "P" in dias:
                    if partido:
                        mapa["P"] = partido[0]
                    else:
                        avisos.append(f"Semana {n} ({nombre.strip()}): hay objetivo de PARTIDOS pero el calendario no marca días de partido; se omite.")
                        dias.pop("P")
                if n in semanas:
                    preferida = ELEGIR.get(n)
                    previa = origen[n]
                    if preferida and norm(preferida) == norm(previa):
                        avisos.append(f"Semana {n}: está en '{previa}' y en '{nombre.strip()}'; uso '{previa}' (elegida).")
                        continue
                    avisos.append(f"Semana {n}: está en '{previa}' y en '{nombre.strip()}'; uso '{nombre.strip()}'." + ("" if preferida else " Confirmar cuál vale."))
                semanas[n] = {"pestana": nombre.strip(), "dias": mapa, "objetivo": dias}
                origen[n] = nombre.strip()
    out = {str(k): semanas[k] for k in sorted(semanas)}
    return out, avisos


def leer_hoja():
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession

    info = json.loads(os.environ["GOOGLE_SA_JSON"])
    cred = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    ses = AuthorizedSession(cred)
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}"
    meta = ses.get(base, params={"fields": "sheets.properties.title"}, timeout=60)
    meta.raise_for_status()
    titulos = [s["properties"]["title"] for s in meta.json()["sheets"]]
    titulos = [t for t in titulos if semana_de_pestana(t) is not False]
    if not titulos:
        return []
    r = ses.get(f"{base}/values:batchGet", timeout=60, params=[
        ("ranges", f"'{t.replace(chr(39), chr(39) * 2)}'!{RANGO_LECTURA}") for t in titulos
    ] + [("valueRenderOption", "UNFORMATTED_VALUE")])
    r.raise_for_status()
    return [(t, vr.get("values", [])) for t, vr in zip(titulos, r.json()["valueRanges"])]


def main():
    if not os.environ.get("GOOGLE_SA_JSON"):
        print("Falta el secret GOOGLE_SA_JSON; no se genera plan.json.")
        sys.exit(1)
    pestanas = leer_hoja()
    semanas, avisos = parsear(pestanas)
    plan = {"generated": datetime.now(timezone.utc).isoformat(), "fuente": "sheets",
            "avisos": avisos, "semanas": semanas, "plantel": {}}
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    print(f"plan.json: {len(semanas)} semanas ({', '.join(semanas)})")
    for a in avisos:
        print("AVISO:", a)


if __name__ == "__main__":
    main()
