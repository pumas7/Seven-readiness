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
PARAMS = [
    "total_distance",
    "velocity_band5_total_distance", "velocity_band6_total_distance",
    "velocity_band7_total_distance", "velocity_band8_total_distance",
    "gen2_velocity_band5_total_effort_count", "gen2_velocity_band6_total_effort_count",
    "gen2_velocity_band7_total_effort_count", "gen2_velocity_band8_total_effort_count",
    "max_vel", "percentage_max_velocity",
    "aceleraciones", "desaceleraciones", "max_effort_acceleration",
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
    """Convierte una fila de /stats en las 10 métricas del tablero."""
    hsr = sum(row.get(f'velocity_band{i}_total_distance', 0) or 0 for i in [5, 6, 7, 8])
    hsr_e = sum(row.get(f'gen2_velocity_band{i}_total_effort_count', 0) or 0 for i in [5, 6, 7, 8])
    ds = sum(row.get(f'velocity_band{i}_total_distance', 0) or 0 for i in [6, 7, 8])
    ds_e = sum(row.get(f'gen2_velocity_band{i}_total_effort_count', 0) or 0 for i in [6, 7, 8])
    return {
        'distancia': round(row.get('total_distance', 0), 1),
        'hsr': round(hsr, 1), 'hsr_entr': hsr_e,
        'ds': round(ds, 1), 'ds_entr': ds_e,
        'vmax': round(row.get('max_vel', 0), 1),
        'pct': round(row.get('percentage_max_velocity', 0), 1),
        'accel': row.get('aceleraciones', 0),
        'deccel': row.get('desaceleraciones', 0),
        'accmax': round(row.get('max_effort_acceleration', 0), 2),
    }


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

    # 3. Para cada semana: traer stats, armar detalle por sesión y TOTAL por jugador
    semanas_data = {}      # {num: {jugador: {total}}}
    sesiones_data = {}     # {num: [ {nombre, fecha, jugadores:{...}} ]}
    for num in sorted(acts_por_semana.keys()):
        acts = sorted(acts_por_semana[num], key=lambda x: x['start_time'])
        act_ids = [a['id'] for a in acts]
        raw = post_stats(act_ids, group_by=["activity", "athlete"])

        # detalle por sesión
        sesiones = {a['id']: {
            'nombre': a['name'],
            'fecha': datetime.fromtimestamp(a['start_time']).strftime('%a %d/%m'),
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

        # TOTAL semanal por jugador
        total = {}
        for j, rows in by_ath.items():
            total[j] = {}
            for k in METRICS:
                vals = [r[k] for r in rows]
                total[j][k] = round(max(vals), 2) if k in MAX_METRICS else round(sum(vals), 1)
            total[j]['sesiones'] = len(rows)

        semanas_data[num] = total
        sesiones_data[num] = [sesiones[a['id']] for a in acts]
        print(f"  Semana {num}: {len(acts)} sesiones, {len(total)} jugadores")

    # 4. Roster unificado con bandas (promedio de semanas reales previas x 0.8 / x 1.3)
    todos = sorted({j for tot in semanas_data.values() for j in tot})
    nums_ordenados = sorted(semanas_data.keys())
    roster = {}
    for j in todos:
        historial = []
        filas = []
        for num in nums_ordenados:
            datos = semanas_data[num].get(j)
            banda = None
            if datos is not None:
                if len(historial) >= 1:
                    metrics_banda = {}
                    for k in METRICS:
                        avg = mean(h[k] for h in historial)
                        metrics_banda[k] = {'low': round(avg * 0.8, 1), 'high': round(avg * 1.3, 1)}
                    banda = {'metrics': metrics_banda, 'n_semanas': len(historial)}
                filas.append({'semana': num, 'datos': datos, 'banda': banda, 'jugo': True})
                historial.append(datos)
            else:
                filas.append({'semana': num, 'datos': None, 'banda': None, 'jugo': False})
        roster[j] = filas

    # 5. Salida
    salida = {
        'generated': hoy.isoformat(),
        'roster': roster,
        'sesiones': {str(k): v for k, v in sesiones_data.items()},
        'semanas': nums_ordenados,
        'sesiones_normales': SESIONES_NORMALES,
    }
    with open('carga.json', 'w', encoding='utf-8') as f:
        json.dump(salida, f, ensure_ascii=False)
    print(f"OK - carga.json generado ({len(roster)} jugadores, {len(nums_ordenados)} semanas)")


if __name__ == "__main__":
    main()
