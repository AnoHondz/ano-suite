from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ano.goal_verification import GoalVerifier
from ano.jobs import JobStore
from ano.plugins.artifacts import Artifacts
from ano.plugins.workshop import Workshop
from ano.tools import Toolbox
from ano.work_ledger import WorkLedger


class GoalVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=JobStore(self.root/'jobs.db')
        project=self.store.create_project('Independent output test')
        self.job=self.store.enqueue(project,'Create requested output');self.store.claim_next()
        self.toolbox=Toolbox(lambda:[self.root],plugins=[Artifacts(Workshop(self.root/'output',None))],
            lifecycle=WorkLedger(self.store,lambda:self.job,output_roots=(self.root/'output',)))
        self.verify=GoalVerifier(self.store)

    def pdf(self):
        result=self.toolbox.run('create_pdf',{'name':'Report','title':'Report','text':'Required literal. Actual file content.'})
        self.assertFalse(result[2]);self.assertNotIn('receipt could not be saved',result[0])

    def test_actual_pdf_is_read_back_and_exact_requested_text_checked(self):
        self.pdf()
        self.assertTrue(self.verify('Create Report.pdf containing "Required literal"',[self.job])['passed'])
        self.assertFalse(self.verify('Create Report.pdf containing "Unwritten text"',[self.job])['passed'])

    def test_wrong_format_and_model_success_prose_cannot_certify_a_goal(self):
        self.pdf()
        self.assertFalse(self.verify('Create a Word document',[self.job])['passed'])
        self.assertFalse(self.verify('Create Report.pdf',[])['passed'])
        self.assertFalse(self.verify('Finish everything',[self.job])['passed'])

    def test_unrelated_job_or_changed_output_cannot_be_used_as_evidence(self):
        self.pdf()
        project=self.store.create_project('Unrelated job');other=self.store.enqueue(project,'Different goal')
        self.assertFalse(self.verify('Create Report.pdf',[other])['passed'])
        path=self.root/'output/Report.pdf';path.write_bytes(path.read_bytes()+b'\nchanged')
        self.assertFalse(self.verify('Create Report.pdf',[self.job])['passed'])

    def test_uncertain_action_blocks_even_when_an_output_exists(self):
        self.pdf();step=self.store.start_step(self.job,'desktop_input',side_effect=True)
        self.store.finish_step(step,'uncertain')
        self.assertFalse(self.verify('Create Report.pdf',[self.job])['passed'])

    def test_deck_checks_the_actual_total_slide_count(self):
        result=self.toolbox.run('create_presentation',{'name':'Pitch','title':'Pitch','slides':[
            {'title':'One','bullets':['First point']},{'title':'Two','bullets':['Second point']}]})
        self.assertFalse(result[2])
        self.assertTrue(self.verify('Create a 3-slide presentation',[self.job])['passed'])
        self.assertFalse(self.verify('Create a 4-slide presentation',[self.job])['passed'])
        self.assertFalse(self.verify('Create four slides',[self.job])['passed'])
        self.toolbox.run('create_presentation',{'name':'Other','title':'Other','slides':[
            {'title':str(i),'bullets':['Point']} for i in range(3)]})
        self.assertFalse(self.verify('Create Pitch.pptx with 4 slides',[self.job])['passed'])

    def test_numbered_steps_are_checked_from_actual_output_text(self):
        self.pdf()
        self.assertFalse(self.verify('Create a PDF with five numbered steps',[self.job])['passed'])
