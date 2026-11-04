"""Bounded health tools; paper readiness never places orders."""
import json
import subprocess
from pathlib import Path
from ..reliability import describe
from ..tools import ToolError


class ReliabilityTools:
    hidden_tools=('open_reliability_dashboard',)
    def __init__(self,service,allowed=lambda:True,authorized=lambda:False):
        self.service,self.allowed,self.authorized=service,allowed,authorized

    def definitions(self):
        return [{'name':'reliability_status','description':'Read Ano/ScorPred service health, paper runner/broker evidence and reliability observations. No orders or email.',
                 'input_schema':{'type':'object','properties':{'verify_broker':{'type':'boolean'}},'additionalProperties':False}}]

    def run(self,name,arguments):
        if not self.allowed():raise ToolError('Ano is paused or locked. No external health check ran.')
        if name=='open_reliability_dashboard':
            if arguments or not self.authorized():raise ToolError('Opening the health dashboard requires a direct user command.')
            root=Path(__file__).resolve().parents[2]
            subprocess.Popen([str(root/'.venv/Scripts/pythonw.exe'),'-m','ano.reliability_dashboard'],cwd=str(root),
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return 'Reliability dashboard launch requested. It reads health and paper readiness; no order or email was sent.',''
        if name!='reliability_status' or set(arguments)-{'verify_broker'} or type(arguments.get('verify_broker',False)) is not bool:
            raise ToolError('Invalid health request.')
        report=self.service.snapshot(verify_broker=arguments.get('verify_broker',False))
        return describe(report),'Read reliability and paper readiness'
