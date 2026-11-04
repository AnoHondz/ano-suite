"""Command-routing audit and live, read-only ScorPred delivery smoke check."""
import ast,json,tempfile,time
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo
from ano.routines import LocalRoutines,RoutineStore,APPS,SITES,MEDIA,CHECKS,DEFAULT_ROUTINES
from ano.plugins.scorpred import ScorPred

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.runtime/command-audit';OUT.mkdir(parents=True,exist_ok=True)

def main():
    result={'at':datetime.now(ZoneInfo('America/New_York')).isoformat(),'routing':[],
            'live':[],'side_effects':'No email, orders, paid AI or desktop actions executed.'}
    with tempfile.TemporaryDirectory() as tmp:
        box=Mock();box.allowed.return_value=True;box.run.return_value=('VERBATIM_TEST_PAYLOAD\n0.123456789\nOVER / UNDER / WAIT','',False)
        local=LocalRoutines(box,RoutineStore(Path(tmp)/'routines.db'),{'documents':str(ROOT),'downloads':str(ROOT)})
        local.install_defaults()
        commands=['open '+x for x in APPS]+['open '+x for x in SITES]+list(MEDIA)+list(CHECKS)
        commands+=['run routine '+x for x in DEFAULT_ROUTINES]
        commands+=['ram status','ano health','scorpred health','scorpred status','scorpred credits',
            'scorpred nba','scorpred soccer','scorpred nfl','scorpred nba details','scorpred nba props',
            'show soccer props','show nfl props','check Naz Reid over 20 points and assists',
            'check Naz Reid under 20 points and assists','morning email','player parlays','nba picks',
            'calculate 24 times 17','calculate 15% of 80','what time is it','what is the date']
        # Include every literal command actually exercised through plan/ask in the test corpus.
        for path in (ROOT/'tests').glob('test_*.py'):
            tree=ast.parse(path.read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr in ('ask','plan') and node.args:
                    arg=node.args[0]
                    if isinstance(arg,ast.Constant) and isinstance(arg.value,str):commands.append(arg.value)
        for command in dict.fromkeys(commands):
            try:
                plan=local.plan(command)
                result['routing'].append({'command':command,'recognized':plan is not None,
                    'tools':[name for name,_ in plan.steps] if plan else [],'uses_model':bool(plan and plan.uses_model)})
            except Exception as e:result['routing'].append({'command':command,'exception':str(e)})
        # Exercise real reply wrapping with distinctive data; the tool body must survive unchanged.
        wrapped=local.reply('scorpred nba')
        result['verbatim_wrapper']=box.run.return_value[0] in wrapped
        plugin=ScorPred(Path(tmp)/'scorpred')
        captured=[]
        real_get=plugin._get
        def get(path,params=None,timeout=30):
            started=time.monotonic();data,status=real_get(path,params,timeout=min(timeout,25))
            captured.append({'path':path,'params':params,'status':status,'seconds':round(time.monotonic()-started,3),'data':data})
            return data,status
        plugin._get=get
        safe={'scorpred_insights','scorpred_props','scorpred_player_prop','scorpred_status','scorpred_credits'}
        for command in ['scorpred status','scorpred credits','scorpred nba','scorpred nba details',
                        'scorpred nba props','scorpred soccer','scorpred nfl',
                        'check Naz Reid over 20 points and assists','check Naz Reid under 20 points and assists']:
            started=time.monotonic();before=len(captured)
            try:
                plan=local.plan(command)
                assert plan and len(plan.steps)==1
                name,args=plan.steps[0];assert name in safe
                body,_=plugin.run(name,args)
                row={'command':command,'ok':True,'text':body,'seconds':round(time.monotonic()-started,3),'api_calls':len(captured)-before}
            except Exception as e:row={'command':command,'ok':False,'error':str(e),'seconds':round(time.monotonic()-started,3)}
            result['live'].append(row)
            (OUT/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        (OUT/'api-evidence.json').write_text(json.dumps(captured,indent=2),encoding='utf-8')
    (OUT/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({'routes':len(result['routing']),'verbatim_wrapper':result['verbatim_wrapper'],
        'live':[{'command':r['command'],'ok':r['ok'],'seconds':r['seconds'],'error':r.get('error')} for r in result['live']]},indent=2))

if __name__=='__main__':main()
