"""
bajar_gimnasio.py — Lee la rutina de GIMNASIO y los CORRECTIVOS / ADAPTACIONES
de la hoja del PF ("Temporada 26-27 Pumas Seven") y genera gimnasio.json para
la pantalla /gimnasio/ del tablero.

Solo LEE la hoja (cuenta de servicio con permiso de Lector). Nunca escribe.

Cada pestaña "Sem N" que tenga el título GIMNASIO es un "plan". Si en una misma
semana hay dos pestañas (ej. "Sem 13" y "Sem 13 b"), quedan las dos como planes
distintos y en la pantalla se elige cuál mostrar.

Todo se ubica por los títulos (GIMNASIO, DIA 1, FUERZA, CORRECTIVOS / ADAPTACIONES),
no por celdas fijas. Lo que no cuadra va a "avisos" y al log del workflow.

Los cambios hechos desde la pantalla NO van acá: se guardan en gimnasio_ajustes.json
y se aplican encima de lo que dice la hoja.
"""
import datetime as dt
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

SPREADSHEET_ID = "1jV5LjeTGaltPHsnH48LSr6xOPxloiv_XFhciBC9C_3k"
SALIDA = "gimnasio.json"
RANGO_LECTURA = "A1:BZ140"

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
ETIQ = re.compile(r"^\s*(A\d+|F\d+|FINISHER|FIN)\s*[:)]\s*", re.I)


def txt(v):
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        # Sheets convierte "6 6 6" en fecha; se reconstruye
        return f"{v.day} {v.month} {v.year % 100}"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).replace("¨", '"').strip()
    # la API devuelve la fecha ya formateada ("6/6/2006"): se vuelve a "6 6 6"
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        s = f"{int(m.group(1))} {int(m.group(2))} {int(m.group(3)) % 100}"
    return s


def norm(v):
    s = unicodedata.normalize("NFD", txt(v))
    return re.sub(r"\s+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip().lower()


def cel(g, r, c):
    return g[r][c] if 0 <= r < len(g) and 0 <= c < len(g[r]) else None


def buscar(g, pred, r0=0, r1=None, c0=0, c1=None):
    for r in range(r0, min(r1 or len(g), len(g))):
        for c in range(c0, min(c1 or 80, len(g[r]))):
            if pred(norm(cel(g, r, c))):
                return r, c
    return None


def semana_de_pestana(nombre):
    """'Sem 13 b' -> 13 ; 'Sem 1-4' -> None (rango) ; otra cosa -> False."""
    m = re.match(r"\s*sem(?:ana)?\s*(\d+)\s*(-\s*\d+)?", nombre, re.I)
    if not m:
        return False
    return None if m.group(2) else int(m.group(1))


def calendario(g):
    """Fila lunes..domingo y el texto de abajo para cada día."""
    for r in range(min(len(g), 20)):
        fila = [norm(cel(g, r, c)) for c in range(12)]
        if "lunes" in fila and "domingo" in fila:
            c0 = fila.index("lunes")
            return {d: norm(cel(g, r + 1, c0 + i)) for i, d in enumerate(DIAS)}
    return {}


def lineas_etiquetadas(texto):
    """'A1: X\\nF1: Y\\nZ' -> [('A1','X'), ('F1','Y'), ('F1','Z')]"""
    out, lab = [], ""
    for ln in [l.strip() for l in txt(texto).split("\n") if l.strip()]:
        m = ETIQ.match(ln)
        if m:
            lab = m.group(1).upper()
            lab = "FINISHER" if lab == "FIN" else lab
            ln = ln[m.end():].strip()
        out.append((lab, ln))
    return out


def emparejar(ejs, dosis):
    """Asigna cada dosis a su ejercicio por etiqueta; lo que no matchea va en orden."""
    items = [{"id": l, "ej": e, "dosis": ""} for l, e in ejs if e]
    for l, d in dosis:
        if not d:
            continue
        dest = next((it for it in items if it["id"] == l and not it["dosis"]), None) if l else None
        if dest is None and l:
            # línea de continuación ("*(progresar tiempo)"): se suma a la dosis de esa etiqueta
            prev = [it for it in items if it["id"] == l and it["dosis"]]
            if prev:
                prev[-1]["dosis"] += " · " + d
                continue
        if dest is None:
            dest = next((it for it in items if not it["dosis"]), None)
        if dest is None:
            if items:
                items[-1]["dosis"] = (items[-1]["dosis"] + " · " + d).strip(" ·")
            continue
        dest["dosis"] = d
    return items


def es_fecha(v):
    return isinstance(v, (dt.datetime, dt.date)) or bool(re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", str(v or "").strip()))


def parsear_gimnasio(g, pestana=""):
    avisos = []
    res = {"dias": {}, "avisos": avisos}
    pos = buscar(g, lambda s: s.startswith("gimnasio"), 0, 10)
    if not pos:
        return None
    rg, cg = pos
    cols, rdia = [], None
    for r in range(rg, rg + 4):
        for c in range(cg, min(len(g[r]) if r < len(g) else 0, cg + 80)):
            m = re.fullmatch(r"dia\s*(\d+)", norm(cel(g, r, c)))
            if m:
                cols.append((m.group(1), c))
        if cols:
            rdia = r
            break
    if not cols:
        avisos.append(f"{pestana}: no encontré DIA 1, DIA 2... arriba del gimnasio.")
        return res
    fz = buscar(g, lambda s: s == "fuerza", rdia, rdia + 15, cg, cg + 2)
    corr = buscar(g, lambda s: s.startswith("correctivos /") or s.startswith("correctivos/"), rdia, rdia + 40, cg, cg + 2)
    rf = fz[0] if fz else rdia + 6
    rc = corr[0] if corr else rf + 12
    fis = buscar(g, lambda s: s.startswith("fisico"), rc, rc + 30, cg, cg + 2)
    if not fz:
        avisos.append(f"{pestana}: no encontré el título FUERZA; asumo fila {rf + 1}.")
    if not corr:
        avisos.append(f"{pestana}: no encontré CORRECTIVOS / ADAPTACIONES; asumo fila {rc + 1}.")

    for i, (n, c) in enumerate(cols):
        cfin = cols[i + 1][1] if i + 1 < len(cols) else c + 7
        previo = [txt(cel(g, r, c)) for r in range(rdia + 1, rf) if txt(cel(g, r, c))]
        cab = {c2: txt(cel(g, rf + 1, c2)).upper() for c2 in range(c + 2, cfin)
               if txt(cel(g, rf + 1, c2)).upper().startswith("SEM")}
        cdos = min(cab) if cab else c + 3
        bloques, cur = [], None
        for r in range(rf + 2, rc):
            lab, ej = txt(cel(g, r, c)).upper(), txt(cel(g, r, c + 1))
            if lab:
                cur = {"id": lab, "items": []}
                bloques.append(cur)
            if not ej:
                continue
            if cur is None:
                cur = {"id": "", "items": []}
                bloques.append(cur)
            if es_fecha(cel(g, r, cdos)):
                avisos.append(f"{pestana} DIA {n}: la dosis de '{ej}' está guardada como fecha en la hoja; "
                              f"se muestra '{txt(cel(g, r, cdos))}'. Conviene escribirla con un apóstrofo adelante.")
            cur["items"].append({"ej": ej, "dosis": txt(cel(g, r, cdos))})
        bloques = [b for b in bloques if b["items"]]
        corrs = []
        for r in range(rc + 2, fis[0] if fis else rc + 20):
            nom = txt(cel(g, r, c))
            if nom and not norm(nom).startswith("correctivos"):
                items = emparejar(lineas_etiquetadas(cel(g, r, c + 1)), lineas_etiquetadas(cel(g, r, c + 3)))
                corrs.append({"jugador": nom.strip(), "items": items})
        res["dias"][n] = {"previo": previo, "bloques": bloques, "correctivos": corrs}
    return res


def armar_planes(pestanas):
    """pestanas: lista de (nombre, grid). Devuelve (planes, avisos)."""
    planes, avisos = {}, []
    for nombre, g in pestanas:
        n = semana_de_pestana(nombre)
        if n is False or n is None:
            continue
        res = parsear_gimnasio(g, nombre.strip())
        if res is None:
            continue  # pestañas viejas sin bloque GIMNASIO
        avisos += res["avisos"]
        if not res["dias"]:
            continue
        cal = calendario(g)
        gym = [d for d in DIAS if "gym" in cal.get(d, "") and not re.search(r"\(\s*sem", cal.get(d, ""))]
        keys = sorted(res["dias"], key=int)
        ds = {k: gym[i] for i, k in enumerate(keys) if i < len(gym)}
        if len(gym) != len(keys):
            avisos.append(f"{nombre.strip()}: el calendario marca {len(gym)} días de gym y la rutina tiene {len(keys)} días.")
        planes[nombre.strip()] = {
            "semana": n,
            "etiqueta": f"{nombre.strip()} · {len(keys)} días de gimnasio",
            "dias_semana": ds,
            "dias": res["dias"],
        }
    return planes, avisos


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
    titulos = [t for t in titulos if semana_de_pestana(t) not in (False, None)]
    if not titulos:
        return []
    rangos = [("ranges", f"'{t.replace(chr(39), chr(39) * 2)}'!{RANGO_LECTURA}") for t in titulos]
    # FORMATTED_VALUE: el texto tal cual se ve en la hoja ("3 X 5 + 5", "75%")
    r = ses.get(f"{base}/values:batchGet", timeout=60,
                params=rangos + [("valueRenderOption", "FORMATTED_VALUE")])
    r.raise_for_status()
    return [(t, vr.get("values", [])) for t, vr in zip(titulos, r.json()["valueRanges"])]


def main():
    if not os.environ.get("GOOGLE_SA_JSON"):
        print("Falta el secret GOOGLE_SA_JSON; no se genera gimnasio.json.")
        sys.exit(1)
    planes, avisos = armar_planes(leer_hoja())
    out = {"generated": datetime.now(timezone.utc).isoformat(), "fuente": "sheets",
           "avisos": avisos, "planes": planes}
    with open(SALIDA, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"gimnasio.json: {len(planes)} planes ({', '.join(planes)})")
    for a in avisos:
        print("AVISO:", a)


if __name__ == "__main__":
    main()
