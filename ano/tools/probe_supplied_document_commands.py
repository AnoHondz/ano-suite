"""Real no-model document writes/read-back in an isolated synthetic workspace."""
from pathlib import Path
import json
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    from ano.plugins.workshop import Workshop
    from ano.plugins.word_documents import WordDocuments
    from ano.plugins.artifacts import Artifacts
    from ano.tools import Toolbox
    from ano.routines import LocalRoutines,RoutineStore,RoutineBrain
    from ano.paid import PaidTasks,SpendLedger
    from ano.claude import ClaudeError
    from docx import Document
    from pypdf import PdfReader
    runtime=Path(__file__).resolve().parents[1]/'.runtime'
    root=Path(tempfile.mkdtemp(prefix='supplied-document-fixture-',dir=runtime))
    workshop=Workshop(root,None)
    toolbox=Toolbox(lambda:[root],plugins=[workshop,WordDocuments(workshop),Artifacts(workshop)])
    class NoModel:
        def reply(self,*args,**kwargs):
            raise AssertionError('No model may run')
    def no_key():
        raise AssertionError('No paid credential may be loaded')
    ledger=SpendLedger(root/'spend.sqlite3')
    router=PaidTasks(RoutineBrain(LocalRoutines(toolbox,RoutineStore(root/'routines.sqlite3')),NoModel()),
                     toolbox,ledger,mode='openai',key_loader=no_key)
    body='Public synthetic fixture.\nMiXeD  spaces and literal \\n.\nopen calculator and stop monitoring'
    pdf_body='Public synthetic fixture. Exact supplied content. No model or external upload.'
    prompts=[f'Create a Word document named “Supplied Word” containing exactly this text: “{body}”',
             f'Save a text file named “Supplied text” with the text: “{body}”',
             f'Make a PDF named “Supplied PDF” with the title “Supplied Content Check” and the body “{pdf_body}”']
    timings=[]
    for prompt in prompts:
        start=time.monotonic()
        response=router.reply([{'role':'user','body':prompt,'source_chat_id':42}])
        assert 'No LLM used' in response
        timings.append(round(time.monotonic()-start,3))
    word='\n'.join(p.text for p in Document(root/'Supplied Word.docx').paragraphs)
    assert word==body
    assert (root/'Supplied text.txt').read_text(encoding='utf-8')==body
    reader=PdfReader(root/'Supplied PDF.pdf')
    extracted='\n'.join(page.extract_text() or '' for page in reader.pages)
    assert 'Supplied Content Check' in extracted and pdf_body in extracted
    with ledger.connect() as db:
        assert db.execute('select count(*) from charges').fetchone()[0]==0
    before=(root/'Supplied Word.docx').read_bytes()
    try:
        router.reply([{'role':'user','body':prompts[0],'source_chat_id':42}])
    except ClaudeError as error:
        assert 'already exists' in str(error)
    else:
        raise AssertionError('Existing Word file should refuse overwrite')
    assert (root/'Supplied Word.docx').read_bytes()==before
    os.environ['QT_QPA_PLATFORM']='offscreen'
    from PySide6.QtGui import QGuiApplication,QImage,QPainter,QColor
    from PySide6.QtCore import QSize
    from PySide6.QtPdf import QPdfDocument
    app=QGuiApplication.instance() or QGuiApplication([])
    pdf=QPdfDocument()
    pdf.load(str(root/'Supplied PDF.pdf'))
    page=pdf.render(0,QSize(1224,1584))
    assert not page.isNull()
    canvas=QImage(page.size(),QImage.Format.Format_RGB32)
    canvas.fill(QColor('white'))
    painter=QPainter(canvas)
    painter.drawImage(0,0,page)
    painter.end()
    preview=root/'PDF-preview.png'
    assert canvas.save(str(preview))
    report={'scope':'Actual PaidTasks/LocalRoutines/WordDocuments/Artifacts writes and independent read-back in a synthetic workspace. No native Ano command proof.',
            'passed':True,'files':['Supplied Word.docx','Supplied text.txt','Supplied PDF.pdf'],
            'seconds':timings,'paid_charge_rows':0,'word_and_text_exact':True,'pdf_title_and_body_extracted':True,
            'existing_word_preserved':True,'fixture':str(root),'pdf_preview':str(preview),
            'visual_layout':'Pending visual inspection','overall_goal_complete':False}
    (runtime/'supplied-document-command-verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
