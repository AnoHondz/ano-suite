"""Small, read-only operational notes for scheduled summaries, never pop-up spam."""
from .reliability import read_json


def notes(state):
    result=[]
    if (state/'supervisor.health.json').exists():
        from .supervisor import observation
        if not observation(state)['fresh']:result.append('Supervisor evidence is stale or its owner is unverified.')
    fx=read_json(state/'forex-execution.health.json')
    for key in ('error','scan_error'):
        if fx.get(key):result.append('Forex '+key+': '+str(fx[key]))
    forecast=read_json(state/'forecast-watch.health.json')
    if forecast.get('state')=='waiting':result.append('Forecast evidence is waiting: '+str(forecast.get('errors') or forecast.get('error_type')))
    if forecast.get('quality_waiting_rows'):result.append(str(forecast['quality_waiting_rows'])+' observed sports rows lacked fresh usable model evidence; picks unavailable.')
    if forecast.get('quality_provisional_rows'):result.append(str(forecast['quality_provisional_rows'])+' observed sports rows allow provisional research picks with incomplete supporting evidence.')
    return result[:4]
