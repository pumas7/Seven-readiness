#!/usr/bin/env python3
"""
Baja datos de VALD (ForceDecks + NordBord) para el tablero de readiness Pumas 7s.
Diseñado para correr en GitHub Actions: las credenciales vienen de variables de entorno.
Genera data.json que el tablero (index.html) lee al abrirse.
"""
import json, subprocess, datetime, os, sys, time, re
 
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
 
 
# --- Estadisticas de la API (para saber si la corrida fue limpia) ---
_API = {"llamadas": 0, "reintentos": 0, "fallos": 0}
# Pausa minima entre llamadas: con ~350 requests seguidos VALD puede empezar a
# rechazar por rate limiting. Ajustable con VALD_PAUSA.
PAUSA = float(os.environ.get("VALD_PAUSA", "0.05"))
REINTENTOS = int(os.environ.get("VALD_REINTENTOS", "4"))


def curl(url, intentos=None):
    """GET a la API de VALD con reintentos y backoff exponencial.

    IMPORTANTE: antes esta funcion devolvia None ante cualquier problema (timeout,
    429, 5xx, JSON cortado) y el resto del script lo interpretaba como "el test no
    tiene metricas". Eso hacia que fallos transitorios de red se disfrazaran de
    datos faltantes en el tablero. Ahora reintenta y, si igual falla, lo grita en
    el log con [ERROR-API] en vez de tragarselo."""
    intentos = intentos or REINTENTOS
    delay = 1.0
    ultimo = ""
    for i in range(intentos):
        if PAUSA:
            time.sleep(PAUSA)
        _API["llamadas"] += 1
        r = subprocess.run(
            ["curl", "-s", "--max-time", "45", "-w", "\n%{http_code}", url,
             "-H", f"Authorization: Bearer {TOKEN}"],
            capture_output=True, text=True)
        body, _, code = r.stdout.rpartition("\n")
        code = code.strip()
        if code == "200":
            try:
                return json.loads(body)
            except Exception:
                # 200 con cuerpo cortado/invalido: vale la pena reintentar
                ultimo = f"HTTP 200 con JSON invalido ({len(body)} chars)"
        elif code in ("400", "404"):
            # Respuesta definitiva del servidor. Es esperable en get_profiles(),
            # que prueba varias URLs hasta dar con la que anda. No se reintenta.
            return None
        elif code in ("401", "403"):
            print(f"    [ERROR-API] {code} (credenciales/permisos) -> {url[:110]}")
            return None
        else:
            ultimo = f"HTTP {code or 'sin respuesta (timeout o red)'}"
        if i < intentos - 1:
            _API["reintentos"] += 1
            time.sleep(delay)
            delay *= 2
    _API["fallos"] += 1
    print(f"    [ERROR-API] sin exito tras {intentos} intentos ({ultimo}) -> {url[:110]}")
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
 
 
# --- Resolucion de la altura de salto del CMJ ---
# El Hub muestra "Jump Height (Imp-Mom)". El codigo "JUMP_HEIGHT" a secas de la API
# es el metodo de tiempo de vuelo, que sobreestima. Como el nombre exacto del codigo
# Imp-Mom puede variar, lo busco por patron entre los codigos que trae el trial.
_JH_USADO = set()
_JH_PATRONES = [
    re.compile(r"^JUMP_HEIGHT.*IMP.*MOM", re.I),
    re.compile(r"IMP.*MOM.*JUMP_HEIGHT", re.I),
    re.compile(r"^CONCENTRIC_IMPULSE.*JUMP_HEIGHT", re.I),
    re.compile(r"JUMP_HEIGHT.*IMPULSE", re.I),
]


def resolver_altura(prom):
    """Devuelve el codigo de altura por impulso-momento presente en el trial, o None.
    Ignora cualquier variante de tiempo de vuelo (FLIGHT_TIME)."""
    candidatos = [c for c in prom if "JUMP_HEIGHT" in c.upper()
                  and "FLIGHT" not in c.upper()
                  and not c.upper().startswith("CMRJ_")]
    for rx in _JH_PATRONES:
        for c in candidatos:
            if rx.search(c):
                return c
    return None


# --- Diagnostico (temporal): permite marcar un jugador para volcar la estructura
# cruda de sus trials fallidos en el log del workflow. Se activa con la variable de
# entorno VALD_DEBUG_PLAYER (ej. "Luciano" alcanza, es substring case-insensitive).
DEBUG_PLAYER = os.environ.get("VALD_DEBUG_PLAYER", "").strip().lower()
_DEBUG_DUMPS = {"n": 0}
_DEBUG_MAX = int(os.environ.get("VALD_DEBUG_MAX_DUMPS", "3"))


def get_trial_metrics(testid, wanted, ctx=None, resolver_jh=False):
    """ctx: dict opcional {"player":..., "test_type":..., "date":...} solo para diagnostico,
    no afecta la logica de extraccion."""
    d = curl(f"{FD}/v2019q3/teams/{TENANT}/tests/{testid}/trials")
    if not d:
        if ctx:
            print(f"    [DIAG {ctx['player']}] {ctx['test_type']} {ctx['date']}: "
                  f"GET /trials devolvio vacio/None para testId={testid}.")
        return {}
    trials = d if isinstance(d, list) else [d]
    # acumulo los valores de cada metrica a lo largo de TODAS las reps y promedio.
    # Guardo TODOS los codigos (no solo los de `wanted`) porque algunos hay que
    # resolverlos por patron, ver resolver_altura() mas abajo.
    acc = {}
    codes_vistos = set()
    for trial in trials:
        for r in trial.get("results", []):
            defin = r.get("definition") or {}
            code = defin.get("result")
            limb = r.get("limb")
            codes_vistos.add((code, limb))
            if code and limb == "Trial":
                v = r.get("value")
                if v is not None:
                    acc.setdefault(code, []).append(v)
    prom = {}
    for code, vals in acc.items():
        if vals:
            prom[code] = round(sum(vals) / len(vals), 4)
    out = {c: v for c, v in prom.items() if c in wanted}
    # Altura de salto del CMJ: el Hub de VALD reporta impulso-momento y el CMRJ ya
    # usa esa variante. El codigo "JUMP_HEIGHT" a secas es tiempo de vuelo, que
    # sobreestima ~10%. Busco la variante Imp-Mom entre lo que el trial realmente
    # trajo, sin depender del nombre exacto que use la API.
    if resolver_jh:
        elegido = resolver_altura(prom)
        if elegido:
            out["JUMP_HEIGHT"] = prom[elegido]
            _JH_USADO.add(elegido)
        elif "JUMP_HEIGHT" in prom:
            _JH_USADO.add("JUMP_HEIGHT (tiempo de vuelo - NO se encontro Imp-Mom)")
    # renombrar las claves Imp-Mom a las que espera el index.html (sin sufijo)
    RENAME = {
        "CMRJ_REBOUND_JUMP_HEIGHT_IMP_MOM": "CMRJ_REBOUND_JUMP_HEIGHT",
        "CMRJ_TAKEOFF_JUMP_HEIGHT_IMP_MOM": "CMRJ_TAKEOFF_JUMP_HEIGHT",
    }
    for old, new in RENAME.items():
        if old in out:
            out[new] = out.pop(old)

    if not out and ctx:
        # Diagnostico liviano (siempre que falla): que (code, limb) trajo realmente el trial,
        # para comparar contra `wanted` + limb=='Trial' sin volcar el JSON entero cada vez.
        print(f"    [DIAG {ctx['player']}] {ctx['test_type']} {ctx['date']} testId={testid}: "
              f"esperaba codigos {wanted} con limb=='Trial'. Encontrado (code, limb) -> "
              f"{sorted((c or '(sin code)', l or '(sin limb)') for c, l in codes_vistos)}")
        # Dump crudo completo, limitado a VALD_DEBUG_MAX_DUMPS veces, solo si VALD_DEBUG_PLAYER
        # matchea el nombre del jugador (para no inundar el log en cada corrida normal).
        if DEBUG_PLAYER and DEBUG_PLAYER in ctx['player'].lower() and _DEBUG_DUMPS["n"] < _DEBUG_MAX:
            _DEBUG_DUMPS["n"] += 1
            raw = json.dumps(trials, indent=2, ensure_ascii=False)
            if len(raw) > 6000:
                raw = raw[:6000] + "\n... [truncado, raw completo mas largo]"
            print(f"    [DIAG-RAW {ctx['player']}] {ctx['test_type']} {ctx['date']} "
                  f"testId={testid}:\n{raw}")
    return out
 
 
print("Detectando plantel del grupo SEVENS desde VALD...")
PLAYERS, dinamico = get_profiles()
print(f"Plantel a procesar: {len(PLAYERS)} jugadores "
      f"({'dinamico' if dinamico else 'fallback fijo'})\n")

out = {"generated": datetime.datetime.now().isoformat(), "players": {}}
_FALTANTES = []

for name, pid in PLAYERS.items():
    print(f"Bajando {name}...")
    pdata = {"cmrj": [], "cmj": [], "nordic": []}
    _dbg = name
    fd = get_all_fd_tests(pid, debug_name=_dbg)
    for t in fd:
        tt = t["testType"]
        fecha = t["recordedDateUtc"][:10]
        if tt == "CMRJ":
            m = get_trial_metrics(t["testId"], WANT_CMRJ,
                                   ctx={"player": name, "test_type": "CMRJ", "date": fecha})
            if m:
                pdata["cmrj"].append({"date": fecha, **m})
        elif tt == "CMJ":
            m = get_trial_metrics(t["testId"], WANT_CMJ,
                                   ctx={"player": name, "test_type": "CMJ", "date": fecha},
                                   resolver_jh=True)
            if m:
                # RSI-mod a m/s (misma escala que VALD Hub)
                if "RSI_MODIFIED" in m and m["RSI_MODIFIED"] and m["RSI_MODIFIED"] > 5:
                    m["RSI_MODIFIED"] = round(m["RSI_MODIFIED"] / 100, 3)
                pdata["cmj"].append({"date": fecha, **m})
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
    # Chequeo de integridad: cuantos tests EXISTEN en VALD vs cuantos pudimos leer.
    esp_cmrj = sum(1 for t in fd if t["testType"] == "CMRJ")
    esp_cmj = sum(1 for t in fd if t["testType"] == "CMJ")
    falt = (esp_cmrj - len(pdata["cmrj"])) + (esp_cmj - len(pdata["cmj"]))
    aviso = ""
    if falt > 0:
        aviso = f"   <-- FALTAN {falt} test(s): esperados CMRJ:{esp_cmrj} CMJ:{esp_cmj}"
        _FALTANTES.append((name, falt))
    print(f"  CMRJ:{len(pdata['cmrj'])} CMJ:{len(pdata['cmj'])} "
          f"Nordic:{len(pdata['nordic'])}{aviso}")
 
print("\n--- Resumen de la corrida ---")
print(f"Altura CMJ tomada de: {sorted(_JH_USADO) or '(ningun CMJ procesado)'}")
print(f"Llamadas a la API VALD: {_API['llamadas']} | "
      f"reintentos: {_API['reintentos']} | fallos definitivos: {_API['fallos']}")
if _FALTANTES:
    print("ATENCION: hay tests en VALD que no se pudieron leer:")
    for n, c in _FALTANTES:
        print(f"  - {n}: faltan {c}")
    print("Estos jugadores van a mostrar datos incompletos en el tablero. "
          "Volver a correr el workflow suele resolverlo si fue un problema de red.")
else:
    print("OK: se extrajeron las metricas de TODOS los tests encontrados en VALD.")
 
with open("data.json", "w") as f:
    json.dump(out, f, indent=2)
 
print("\nLISTO. data.json generado.")
