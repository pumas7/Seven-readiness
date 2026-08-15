#!/usr/bin/env python3
"""
Baja datos de carga externa (Catapult OpenField) para el tablero de Pumas 7s.
Diseñado para correr en GitHub Actions: el token viene de una variable de entorno.
Genera carga.json que el tablero (carga/index.html) lee al abrirse.

Lógica:
- Trae todas las actividades del grupo SEVEN desde INICIO_TEMPORADA hasta hoy.
- Agrupa por semana calendario (lunes a domingo).
- Roster dinámico: detecta automáticamente qué jugadores tuvo cada semana.
- Calcula el TOTAL semanal (suma para volumen, máximo para picos).
- Calcula la banda de referencia (promedio de semanas reales previas x 0.8 y x 1.3).
"""
import json, os, sys, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean

# --- Credenciales desde variable de entorno (GitHub Secret) ---
TOKEN = os.environ.get("CATAPULT_TOKEN")
if not TOKEN:
    print("ERROR: falta la variable CATAPULT_TOKEN")
    sys.exit(1)

BASE = "https://connect-us.catapultsports.com/api/v4"
OWNER_SEVEN = "SEVEN"                      # owner.name del grupo Sevens
INICIO_TEMPORADA = datetime(2026, 7, 20)   # lunes, semana 1 día 1
SESIONES_NORMALES = 4                       # referencia semanal (pretemporada)

# Mapeo de parámetros Catapult (validado)
# Slugs tomados del tenant de la UAR (los mismos que usa el PF en su Excel).
# OJO: en este tenant hay parametros distintos con el mismo nombre visible
# (ej. dos "Aceleraciones"), asi que siempre usar el slug, nunca el nombre.
HSR_ENTR_SLUG = "hsr_efforts"   # alternativa a probar: "#_esf_hsr_1"

PARAMS = [
    "total_distance",
    # HSR distancia: suma de bandas (ya coincidia con el Excel del PF)
    "velocity_band5_total_distance", "velocity_band6_total_distance",
    "velocity_band7_total_distance", "velocity_band8_total_distance",
    # HSR entradas: se piden los dos candidatos para poder compararlos
    "hsr_efforts", "#_esf_hsr_1",
    "dt_+25,2_km/h",        # DS / Sprint (m)  -> corte real en 25,2 km/h
    "sprint_sevens_esf",    # DS entradas
    "max_vel", "percentage_max_velocity",
    "gen2_acceleration_band7plus_total_effort_count",  # Aceleraciones B2-3
    "gen2_acceleration_band2plus_total_effort_count",  # Desaceleraciones B2-3
    "max_effort_acceleration",
]
METRICS = ['distancia','hsr','hsr_entr','ds','ds_entr','vmax','pct','accel','deccel','accmax']
MAX_METRICS = {'vmax','pct','accmax'}  # estas se agregan por MÁXIMO, el resto por SUMA


def get(url):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}", "Accept": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def post_stats(activity_ids, group_by):
    body = {
        "filters": [{"name": "activity_id", "comparison": "=", "values": activity_ids}],
        "parameters": PARAMS, "group_by": group_by,
    }
    req = urllib.request.Request(
        f"{BASE}/stats", data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {TOKEN}", "Accept": "application/json",
                 "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# --- Alias de perfiles Catapult ---
# Jugadores que entrenaron con un GPS/perfil generico antes de tener su perfil real
# creado. La clave es el nombre generico como viene de Catapult (normalizado);
# el valor, el nombre real al que se fusionan sus sesiones.
# Para agregar uno nuevo: 'Nombre Generico': 'Nombre Real'.
ALIAS = {
    'Jugador Uno':  'Ignacio Diaz',
    'Jugador Dos':  'Francisco Calello',
    'Jugador Tres': 'Juan Cruz Massun',
}


def norm(name):
    n = ' '.join(w.capitalize() for w in name.strip().split())
    return ALIAS.get(n, n)


def combinar(a, b):
    """Fusiona dos filas de metricas de la MISMA sesion (mismo jugador, dos perfiles).
    Volumen se suma, picos se toman al maximo. Evita contar sesiones duplicadas."""
    out = {}
    for k in METRICS:
        va, vb = a.get(k, 0), b.get(k, 0)
        out[k] = round(max(va, vb), 2) if k in MAX_METRICS else round(va + vb, 1)
    return out


def semana_de(fecha):
    """Devuelve el número de semana (1-based) según INICIO_TEMPORADA, lunes a domingo."""
    dias = (fecha.date() - INICIO_TEMPORADA.date()).days
    return dias // 7 + 1


def fila_metrica(row):
    """Convierte una fila de /stats en las 10 métricas del tablero.
    DS, DS entradas, HSR entradas, acel y decel salen de parametros propios
    del tenant, no de sumas de bandas: las sumas daban de mas porque los
    cortes de banda no son los que usa el PF."""
    hsr = sum(row.get(f'velocity_band{i}_total_distance', 0) or 0 for i in [5, 6, 7, 8])
    return {
        'distancia': round(row.get('total_distance', 0) or 0, 1),
        'hsr': round(hsr, 1),
        'hsr_entr': row.get(HSR_ENTR_SLUG, 0) or 0,
        'ds': round(row.get('dt_+25,2_km/h', 0) or 0, 1),
        'ds_entr': row.get('sprint_sevens_esf', 0) or 0,
        'vmax': round(row.get('max_vel', 0) or 0, 1),
        'pct': round(row.get('percentage_max_velocity', 0) or 0, 1),
        'accel': row.get('gen2_acceleration_band7plus_total_effort_count', 0) or 0,
        'deccel': row.get('gen2_acceleration_band2plus_total_effort_count', 0) or 0,
        'accmax': round(row.get('max_effort_acceleration', 0) or 0, 2),
    }


MANUAL_PATH = 'manual.json'


def leer_manual():
    """Lee manual.json (notas del PF + sesiones cargadas a mano).
    Si no existe o esta roto, devuelve una estructura vacia y sigue."""
    vacio = {'notas': {}, 'sesiones': []}
    if not os.path.exists(MANUAL_PATH):
        print('  manual.json no existe todavia, sigo solo con Catapult')
        return vacio
    try:
        with open(MANUAL_PATH, encoding='utf-8') as f:
            m = json.load(f)
    except Exception as e:
        print('  ATENCION: manual.json ilegible (%s), lo ignoro' % e)
        return vacio
    m.setdefault('notas', {})
    m.setdefault('sesiones', [])
    print('  manual.json: %d notas, %d sesiones manuales'
          % (len(m['notas']), len(m['sesiones'])))
    return m


def fila_manual(d):
    """Normaliza las metricas de una sesion manual al mismo shape que Catapult."""
    out = {}
    for k in METRICS:
        v = d.get(k, 0) or 0
        out[k] = round(float(v), 2) if k in MAX_METRICS else round(float(v), 1)
    return out


def totalizar(by_ath):
    """{jugador: [filas]} -> {jugador: {metrica: total, sesiones: n}}"""
    total = {}
    for j, rows in by_ath.items():
        total[j] = {}
        for k in METRICS:
            vals = [r[k] for r in rows]
            total[j][k] = round(max(vals), 2) if k in MAX_METRICS else round(sum(vals), 1)
        total[j]['sesiones'] = len(rows)
    return total


def bandas_de(historial):
    """Banda de referencia = promedio de semanas reales previas x0.8 / x1.3."""
    if len(historial) < 1:
        return None
    metrics_banda = {}
    for k in METRICS:
        avg = mean(h[k] for h in historial)
        metrics_banda[k] = {'low': round(avg * 0.8, 1), 'high': round(avg * 1.3, 1)}
    return {'metrics': metrics_banda, 'n_semanas': len(historial)}


def main():
    hoy = datetime.now()
    print(f"Bajando datos SEVEN desde {INICIO_TEMPORADA.date()} hasta {hoy.date()}...")

    # 1. Traer todas las actividades y filtrar SEVEN dentro del rango
    data = get(f"{BASE}/activities?limit=500")
    sevens = []
    for a in data:
        if a['owner']['name'] != OWNER_SEVEN:
            continue
        dt = datetime.fromtimestamp(a['start_time'])
        if INICIO_TEMPORADA <= dt <= hoy.replace(hour=23, minute=59, second=59):
            sevens.append(a)
    sevens.sort(key=lambda x: x['start_time'])
    print(f"  {len(sevens)} actividades SEVEN encontradas")

    # 2. Agrupar actividades por semana
    acts_por_semana = defaultdict(list)
    for a in sevens:
        dt = datetime.fromtimestamp(a['start_time'])
        acts_por_semana[semana_de(dt)].append(a)

    # 2b. Sesiones manuales del PF, agrupadas por semana
    manual = leer_manual()
    man_por_semana = defaultdict(list)
    for ms in manual['sesiones']:
        try:
            man_por_semana[int(ms['semana'])].append(ms)
        except (KeyError, ValueError, TypeError):
            print('  ATENCION: sesion manual sin semana valida, la salteo')

    # 3. Para cada semana: traer stats, armar detalle por sesión y TOTAL por jugador
    semanas_data = {}       # {num: {jugador: total}}  <- incluye manuales
    semanas_real = {}       # {num: {jugador: total}}  <- solo Catapult
    sesiones_data = {}      # {num: [ {nombre, fecha, origen, jugadores:{...}} ]}
    semanas_todas = sorted(set(acts_por_semana.keys()) | set(man_por_semana.keys()))
    for num in semanas_todas:
        acts = sorted(acts_por_semana.get(num, []), key=lambda x: x['start_time'])
        act_ids = [a['id'] for a in acts]
        raw = post_stats(act_ids, group_by=["activity", "athlete"]) if act_ids else []

        # diagnostico: los dos candidatos de HSR entradas, para elegir con datos
        if raw and num == semanas_todas[0]:
            print("  --- HSR entradas: comparacion de slugs (semana %d) ---" % num)
            for r0 in raw[:6]:
                print("      %-26s hsr_efforts=%-6s #_esf_hsr_1=%-6s ds=%-8s ds_esf=%-5s ac=%-5s dc=%s"
                      % (r0.get('athlete_name', '')[:26],
                         r0.get('hsr_efforts'), r0.get('#_esf_hsr_1'),
                         r0.get('dt_+25,2_km/h'), r0.get('sprint_sevens_esf'),
                         r0.get('gen2_acceleration_band7plus_total_effort_count'),
                         r0.get('gen2_acceleration_band2plus_total_effort_count')))

        # detalle por sesión
        sesiones = {a['id']: {
            'nombre': a['name'],
            'fecha': datetime.fromtimestamp(a['start_time']).strftime('%a %d/%m'),
            'origen': 'catapult',
            'jugadores': {}
        } for a in acts}
        # acumulador por jugador para el TOTAL
        by_ath = defaultdict(list)
        sueltos = defaultdict(list)
        for row in raw:
            aid = row.get('activity_id')
            m = fila_metrica(row)
            j = norm(row['athlete_name'])
            if aid in sesiones:
                previo = sesiones[aid]['jugadores'].get(j)
                if previo is not None:
                    # mismo jugador con dos perfiles en la MISMA sesion: fusionar,
                    # no contar dos sesiones
                    print(f"  ! {j}: dos perfiles en '{sesiones[aid]['nombre']}', fusionados")
                    m = combinar(previo, m)
                sesiones[aid]['jugadores'][j] = m
            else:
                sueltos[j].append(m)

        # el TOTAL se arma desde el detalle ya fusionado -> 1 sesion = 1 fila
        for s in sesiones.values():
            for j, m in s['jugadores'].items():
                by_ath[j].append(m)
        for j, ms in sueltos.items():
            by_ath[j].extend(ms)

        # TOTAL semanal contando SOLO lo medido por Catapult
        semanas_real[num] = totalizar(by_ath)

        # sumar las sesiones manuales de esta semana
        lista_ses = [sesiones[a['id']] for a in acts]
        n_man = 0
        for ms in sorted(man_por_semana.get(num, []), key=lambda x: x.get('fecha', '')):
            jugadores_ms = {}
            for jn, met in (ms.get('jugadores') or {}).items():
                fila = fila_manual(met)
                jugadores_ms[norm(jn)] = fila
                by_ath[norm(jn)].append(fila)
            lista_ses.append({
                'nombre': ms.get('nombre', 'Sesión manual'),
                'fecha': ms.get('fecha', ''),
                'origen': 'manual',
                'motivo': ms.get('motivo', ''),
                'autor': ms.get('autor', ''),
                'creado': ms.get('creado', ''),
                'id_manual': ms.get('id', ''),
                'jugadores': jugadores_ms,
            })
            n_man += 1

        semanas_data[num] = totalizar(by_ath)
        sesiones_data[num] = lista_ses
        extra = f" (+{n_man} manual{'es' if n_man != 1 else ''})" if n_man else ''
        print(f"  Semana {num}: {len(acts)} sesiones{extra}, {len(semanas_data[num])} jugadores")

    # 4. Roster unificado. Se calculan DOS series en paralelo:
    #    - datos/banda           -> incluye sesiones manuales (lo que se ve por defecto)
    #    - datos_real/banda_real -> solo Catapult (para auditar una decision)
    todos = sorted({j for tot in semanas_data.values() for j in tot})
    nums_ordenados = sorted(semanas_data.keys())
    semanas_con_manual = {num for num, l in sesiones_data.items()
                          if any(s.get('origen') == 'manual' for s in l)}
    roster = {}
    for j in todos:
        historial, historial_real = [], []
        filas = []
        for num in nums_ordenados:
            datos = semanas_data[num].get(j)
            datos_real = semanas_real.get(num, {}).get(j)
            if datos is None:
                filas.append({'semana': num, 'datos': None, 'banda': None,
                              'datos_real': None, 'banda_real': None,
                              'jugo': False, 'tiene_manual': False})
                continue
            banda = bandas_de(historial)
            banda_real = bandas_de(historial_real)
            tiene_manual = (num in semanas_con_manual
                            and datos_real is not None
                            and datos.get('sesiones') != datos_real.get('sesiones'))
            filas.append({'semana': num, 'datos': datos, 'banda': banda,
                          'datos_real': datos_real, 'banda_real': banda_real,
                          'jugo': True, 'tiene_manual': tiene_manual})
            historial.append(datos)
            if datos_real is not None:
                historial_real.append(datos_real)
        roster[j] = filas

    # 5. Salida
    salida = {
        'generated': hoy.isoformat(),
        'roster': roster,
        'sesiones': {str(k): v for k, v in sesiones_data.items()},
        'semanas': nums_ordenados,
        'sesiones_normales': SESIONES_NORMALES,
        'notas': manual.get('notas', {}),
        'n_manuales': sum(1 for l in sesiones_data.values()
                          for s in l if s.get('origen') == 'manual'),
    }
    with open('carga.json', 'w', encoding='utf-8') as f:
        json.dump(salida, f, ensure_ascii=False)
    print(f"OK - carga.json generado ({len(roster)} jugadores, {len(nums_ordenados)} semanas)")


if __name__ == "__main__":
    main()
