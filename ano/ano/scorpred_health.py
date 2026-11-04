"""Separate ScorPred availability from its data-pipeline readiness."""
def assess(service,pipelines):
    if service.get('status')!='ok' or service.get('db')!='connected':
        return 'blocked','Service/database is not ready.'
    rows=pipelines.get('pipelines')
    if not isinstance(rows,dict) or not rows:
        return 'degraded','Service is online; pipeline health is unverified.'
    problems=[]
    for name,row in rows.items():
        if not isinstance(row,dict) or row.get('status')!='ok':
            detail=(row.get('error') or row.get('status') or 'unverified') if isinstance(row,dict) else 'unverified'
            problems.append(f'{name}: {detail}')
    if problems or pipelines.get('overall')!='ok' or service.get('degraded_mode') or service.get('api') in ('degraded','error','offline'):
        return 'degraded','Service online; '+('; '.join(problems) if problems else 'data API reports degraded readiness.')
    return 'healthy','Service, database and all reported data pipelines are healthy.'
