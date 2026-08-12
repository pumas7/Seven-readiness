#!/usr/bin/env python3
"""
Baja datos de VALD (ForceDecks + NordBord) para el tablero de readiness Pumas 7s.
Diseñado para correr en GitHub Actions: las credenciales vienen de variables de entorno.
Genera data.json que el tablero (index.html) lee al abrirse.
"""
import json, subprocess, datetime, os, sys
 
# --- Credenciales desde variables de entorno (GitHub Secrets) ---
CLIENT_ID = os.environ.get("VALD_CLIENT_ID")
CLIENT_SECRET = os.environ.get("VALD_CLIENT_SECRET")
TENANT = os.environ.get("VALD_TENANT_ID", "d51dc397-c103-4cb5-8076-ecd5ccac9274")
 
if not CLIENT_ID or not CLIENT_SECRET:
    print("ERROR: faltan las variables VALD_CLIENT_ID / VALD_CLIENT_SECRET")
    sys.exit(1)
 
FD = "https://prd-use-api-extforcedecks.valdperformance.com"
NB = "https://prd-use-api-externalnordbord.valdperformance.com"
PROFILE = "https://prd-use-api-externalprofile.valdperformance.com"
TENANTS = "https://prd-use-api-externaltenants.valdperformance.com"
DESDE = "2024-01-01T00:00:00.000Z"

# Grupo del que queremos traer TODOS los jugadores automaticamente (igual que Catapult -> SEVEN).
# Se compara sin distinguir mayus/minus y contra varios campos posibles de la API de profiles.
GRUPO_OBJETIVO = os.environ.get("VALD_GRUPO", "SEVENS")

# Lista fija de respaldo (fallback): si la API de profiles falla o no devuelve nada,
# usamos estos IDs para no quedarnos sin datos. El plantel real se detecta dinamicamente.
PLAYERS_FALLBACK = {
    "Santiago Alvarez Fourcade": "5f5fd3cb-baa4-4989-bdce-b306a6bd471b",
    "Martiniano Arrieta": "435762d5-58b0-48a1-903d-712f9620ec2f",
    "Juan Patricio Batac": "730f56a7-a250-4d2c-bc86-35443b02b22f",
    "Pedro De Haro": "393271ad-c005-4e28-b5b4-1775644e6708",
    "Sebastian Dubuc": "d7e1341d-c274-406e-8103-9cb767c000c1",
    "Luciano Gonzalez Rizzoni": "db71bc95-2c88-4c78-99d3-34a6519c9728",
    "Matteo Graziano": "dd3e8340-ddb6-4dd6-9a9f-b243393ec6aa",
    "Santiago Mare": "78c12530-5cd3-4ed1-9dff-94530d2e19d4",
    "Marcos Moneta": "da3bdbc2-6fd1-4b3c-889b-77281b004d7d",
    "Gregorio Perez Pardo": "687215e3-b505-4bce-bd58-61eb3e292ce6",
    "Joaquin Pellandini": "2935c7bc-f6cf-46b9-9376-d084bba1bdbe",
    "Santiago Vera Feld": "1fbbc443-b63a-4e5c-adca-954d8450ec3a",
    "Santino Zangara": "5fcd704c-10d2-471a-a8b2-1ee9d316f720",
}
 
WANT_CMRJ = ["CMRJ_REBOUND_RSI", "CMRJ_REBOUND_CONTACT_TIME", "CMRJ_REBOUND_ECC_DURATION",
             "CMRJ_REBOUND_JUMP_HEIGHT_IMP_MOM", "CMRJ_TAKEOFF_JUMP_HEIGHT_IMP_MOM"]
WANT_CMJ = ["RSI_MODIFIED", "CONTRACTION_TIME", "ECCENTRIC_PEAK_VELOCITY", "JUMP_HEIGHT", "BODY_WEIGHT"]
 
 
def get_token():
    r = subprocess.run([
        "curl", "-s", "-X", "POST", "https://auth.prd.vald.com/oauth/token",
        "-H", "Content-Type: application/x-www-form-urlencoded",
        "-d", "grant_type=client_credentials",
        "-d", f"client_id={CLIENT_ID}",
        "-d", f"client_secret={CLIENT_SECRET}",
        "-d", "audience=vald-api-external"
    ], capture_output=True, text=True)
    try:
        return json.loads(r.stdout)["access_token"]
    except Exception:
        return None
 
 
print("Generando token...")
TOKEN = get_token()
if not TOKEN:
    print("ERROR: no se pudo generar el token. Revisar credenciales.")
    sys.exit(1)
print("Token OK\n")
 
 
def curl(url):
    r = subprocess.run(["curl", "-s", url, "-H", f"Authorization: Bearer {TOKEN}"],
                       capture_output=True, text=True)
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def _nombre_de(p):
    n = (f"{p.get('givenName','')} {p.get('familyName','')}".strip()
         or p.get("fullName") or p.get("name") or "")
    return " ".join(w.capitalize() for w in n.split())


def get_groups():
    """Lista los grupos del tenant en VALD usando el endpoint de tenants (el que funciona
    para este tenant). Devuelve [{id, name}]. Deja un fallback de diagnostico por si el
    endpoint cambia en el futuro."""
    # Endpoint confirmado para el tenant UAR: servicio de tenants, /groups.
    url = f"{TENANTS}/groups?TenantId={TENANT}"
    d = curl(url)
    grupos = None
    if isinstance(d, dict):
        grupos = d.get("groups") or d.get("items") or d.get("data")
    elif isinstance(d, list):
        grupos = d
    if grupos:
        out = []
        for g in grupos:
            if isinstance(g, dict):
                gid = g.get("id") or g.get("groupId")
                gname = g.get("name") or g.get("groupName") or ""
                if gid:
                    out.append({"id": gid, "name": gname})
        if out:
            print(f"  [GROUPS] {len(out)} grupos en el tenant.")
            return out
    print("  [GROUPS] el endpoint de grupos no respondio como se esperaba.")
    return []




def _fetch_lista(url):
    """GET a un endpoint que devuelve una lista de perfiles bajo varias claves posibles."""
    d = curl(url)
    if isinstance(d, dict):
        return d.get("profiles") or d.get("items") or d.get("data")
    if isinstance(d, list):
        return d
    return None


def get_profiles():
    """Detecta el plantel de Sevens usando el EXTERNAL ID como criterio.
    El staff carga un External ID en VALD a cada jugador de Sevens (numero simple).
    El script trae los perfiles del tenant y se queda SOLO con los que tienen External ID
    cargado -> ese perfil es el correcto (el mismo del Hub) y su profileId sirve para tests.
    Esto evita el lio de que VALD maneja IDs distintos por servicio.
    Si nadie tiene External ID, cae al filtrado por grupo; y si eso falla, al fallback fijo."""

    # --- Estrategia principal: filtrar por External ID cargado ---
    profs_all = _fetch_lista(f"{PROFILE}/profiles?TenantId={TENANT}")
    if profs_all:
        players = {}
        con_extid = []
        for p in profs_all:
            extid = (p.get("externalId") or "").strip()
            pid = p.get("profileId") or p.get("id")
            nombre = _nombre_de(p)
            if extid and pid and nombre:
                players[nombre] = pid
                con_extid.append(f"{nombre} (extId={extid})")
        if players:
            print(f"  [PROFILES] {len(players)} jugadores con External ID (metodo externalId):")
            for c in con_extid:
                print(f"     - {c}")
            return players, True
        else:
            print("  [PROFILES] ningun perfil tiene External ID cargado; probando por grupo...")

    # --- Fallback 1: filtrar por grupo SEVENS (metodo anterior) ---
    objetivo = GRUPO_OBJETIVO.strip().lower()
    grupos = get_groups()
    grupo_sevens = None
    for g in (grupos or []):
        gname = (g.get("name") or "").strip().lower()
        if gname and (objetivo in gname or gname in objetivo):
            grupo_sevens = g
            break
    if grupo_sevens:
        gid = grupo_sevens["id"]
        print(f"  [PROFILES] grupo '{grupo_sevens['name']}' identificado (id={gid}).")
        profs_grupo = None
        for url in (f"{PROFILE}/profiles?TenantId={TENANT}&GroupId={gid}",
                    f"{PROFILE}/profiles?TenantId={TENANT}&groupId={gid}",
                    f"{PROFILE}/groups/{gid}/profiles?TenantId={TENANT}"):
            profs_grupo = _fetch_lista(url)
            if profs_grupo:
                break
        if profs_grupo:
            nombres_sevens = {}
            for p in profs_grupo:
                n = _nombre_de(p)
                if n:
                    nombres_sevens[n.lower()] = n
            id_por_nombre = {}
            for p in (profs_all or []):
                n = _nombre_de(p)
                pid = p.get("profileId") or p.get("id")
                if n and pid:
                    id_por_nombre[n.lower()] = pid
            fijo_por_nombre = {k.lower(): v for k, v in PLAYERS_FALLBACK.items()}
            players = {}
            for nlow, ndisp in nombres_sevens.items():
                pid = fijo_por_nombre.get(nlow) or id_por_nombre.get(nlow)
                if pid:
                    players[ndisp] = pid
            if players:
                print(f"  [PROFILES] {len(players)} jugadores por grupo (fallback IDs cruzados).")
                return players, True

    # --- Fallback 2: lista fija ---
    print("  [PROFILES] usando fallback fijo (13 jugadores).")
    return dict(PLAYERS_FALLBACK), False

 
 

 
 
def get_all_fd_tests(pid, debug_name=None):
    frm = DESDE
    allt = []
    seen = set()
    for _ in range(100):
        url = f"{FD}/tests?TenantId={TENANT}&ModifiedFromUtc={frm}&ProfileId={pid}"
        d = curl(url)
        if not d or "tests" not in d:
            if debug_name:
                print(f"    [DEBUG {debug_name}] respuesta sin 'tests'. Raw: {str(d)[:200]}")
            break
        ts = d["tests"]
        if not ts:
            break
        nuevos = [t for t in ts if t["testId"] not in seen]
        for t in nuevos:
            seen.add(t["testId"])
        allt += nuevos
        # CORTE CONFIABLE: si esta pagina no aporto ningun test nuevo, terminamos.
        if not nuevos:
            break
        # avanzar el cursor. Sumo 1 milisegundo al max para NO volver a pedir el mismo borde
        # (evita quedarse pidiendo la misma pagina y evita saltear tests del mismo instante).
        last = max(t["modifiedDateUtc"] for t in ts)
        try:
            dt = datetime.datetime.fromisoformat(last.replace("Z", "+00:00"))
            dt = dt + datetime.timedelta(milliseconds=1)
            frm = dt.isoformat().replace("+00:00", "Z")
        except Exception:
            frm = last
    if debug_name:
        tipos = {}
        for t in allt:
            tipos[t.get("testType")] = tipos.get(t.get("testType"), 0) + 1
        print(f"    [DEBUG {debug_name}] total tests FD: {len(allt)} | por tipo: {tipos}")
    return allt
 
 
def get_trial_metrics(testid, wanted):
    d = curl(f"{FD}/v2019q3/teams/{TENANT}/tests/{testid}/trials")
    if not d:
        return {}
    trials = d if isinstance(d, list) else [d]
    # acumulo los valores de cada metrica a lo largo de TODAS las reps y promedio
    acc = {}
    for trial in trials:
        for r in trial.get("results", []):
            code = r["definition"]["result"]
            if code in wanted and r.get("limb") == "Trial":
                v = r.get("value")
                if v is not None:
                    acc.setdefault(code, []).append(v)
    out = {}
    for code, vals in acc.items():
        if vals:
            out[code] = round(sum(vals) / len(vals), 4)
    # renombrar las claves Imp-Mom a las que espera el index.html (sin sufijo)
    RENAME = {
        "CMRJ_REBOUND_JUMP_HEIGHT_IMP_MOM": "CMRJ_REBOUND_JUMP_HEIGHT",
        "CMRJ_TAKEOFF_JUMP_HEIGHT_IMP_MOM": "CMRJ_TAKEOFF_JUMP_HEIGHT",
    }
    for old, new in RENAME.items():
        if old in out:
            out[new] = out.pop(old)
    return out
 
 
print("Detectando plantel del grupo SEVENS desde VALD...")
PLAYERS, dinamico = get_profiles()
print(f"Plantel a procesar: {len(PLAYERS)} jugadores "
      f"({'dinamico' if dinamico else 'fallback fijo'})\n")

out = {"generated": datetime.datetime.now().isoformat(), "players": {}}

for name, pid in PLAYERS.items():
    print(f"Bajando {name}...")
    pdata = {"cmrj": [], "cmj": [], "nordic": []}
    _dbg = name
    fd = get_all_fd_tests(pid, debug_name=_dbg)
    for t in fd:
        tt = t["testType"]
        if tt == "CMRJ":
            m = get_trial_metrics(t["testId"], WANT_CMRJ)
            if _dbg and not m:
                print(f"    [DEBUG {_dbg}] CMRJ {t['recordedDateUtc'][:10]} SIN metricas (trial vacio o codigos no coinciden)")
            if m:
                pdata["cmrj"].append({"date": t["recordedDateUtc"][:10], **m})
        elif tt == "CMJ":
            m = get_trial_metrics(t["testId"], WANT_CMJ)
            if _dbg and not m:
                print(f"    [DEBUG {_dbg}] CMJ {t['recordedDateUtc'][:10]} SIN metricas")
            if m:
                # RSI-mod a m/s (misma escala que VALD Hub)
                if "RSI_MODIFIED" in m and m["RSI_MODIFIED"] and m["RSI_MODIFIED"] > 5:
                    m["RSI_MODIFIED"] = round(m["RSI_MODIFIED"] / 100, 3)
                pdata["cmj"].append({"date": t["recordedDateUtc"][:10], **m})
    nb = curl(f"{NB}/tests/v2?TenantId={TENANT}&ModifiedFromUtc={DESDE}&ProfileId={pid}")
    if nb and "tests" in nb:
        for t in nb["tests"]:
            li, re = t.get("leftMaxForce", 0), t.get("rightMaxForce", 0)
            lt, rt = t.get("leftTorque", 0), t.get("rightTorque", 0)
            bw = t.get("bodyWeight") or t.get("weight") or 0
            asym = round((re - li) / max(li, re) * 100, 1) if max(li, re) > 0 else 0
            reps = t.get("repetitions") or t.get("reps") or None
            pdata["nordic"].append({
                "date": t["testDateUtc"][:10], "leftMaxForce": li, "rightMaxForce": re,
                "asym": asym, "leftTorque": lt, "rightTorque": rt, "bodyWeight": bw, "reps": reps
            })
    for k in pdata:
        pdata[k].sort(key=lambda x: x["date"])
    out["players"][name] = pdata
    print(f"  CMRJ:{len(pdata['cmrj'])} CMJ:{len(pdata['cmj'])} Nordic:{len(pdata['nordic'])}")
 
with open("data.json", "w") as f:
    json.dump(out, f, indent=2)
 
print("\nLISTO. data.json generado.")
