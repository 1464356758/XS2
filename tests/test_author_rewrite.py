"""仅在临时合成项目验证授权重写、版本保留和幂等。"""
from pathlib import Path
import shutil
import sys
from test_novel_state import WorkspaceFixture, ns
import test_serial_release as fixtures
sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import author_rewrite as rw
import daily_update as daily

class RewriteTests(WorkspaceFixture):
    prepare=fixtures.SerialTests.prepare;gate=fixtures.SerialTests.gate;release=fixtures.SerialTests.release
    def setUp(self):
        super().setUp();self.project=self.root.parent
        self.serial=ns.serial_module();self.a=ns.serial_api();self.serial.enable(self.root,'合成测试',ns.load_state(self.root)['revision'],self.a)
        self.release(1);first=daily.begin(self.project,trigger_id='synthetic_original_trigger');self.release(2);self.receipt=daily.finish(self.project,first['run_id'])
        state=ns.load_state(self.root);state['tasks']['2']['repairs']=2;ns.atomic_json(self.root/'state.json',state)
        self.owner=dict(kind='automation',automation_id='synthetic_automation',session_id='synthetic_session',platform_run_id=None,invocation_kind='author_manual')
        ns.atomic_json(self.project/'.automation/config.json',dict(book_id=state['book_id'],automation_id=self.owner['automation_id'],completion_policy='per_trigger'))
        ns.atomic_json(self.project/'project_config.json',{'authority':{'repository_url':'https://github.com/example/synthetic','branch':'main'}})
        ns.atomic_json(self.root/'workflow.json',dict(schema_version=1,revision=0,book_id=state['book_id'],phase='WAITING_NEXT',platform='synthetic',chapter=3,history=[]))
        shutil.copytree(Path(rw.__file__).parent,self.project/'tools',ignore=shutil.ignore_patterns('__pycache__'))
        (self.project/'drafts').mkdir()
        self.g=dict(type='novel-author-rewrite-v1',authorization_basis='explicit_author_rewrite_request',author_instruction='合成作者授权重新写第二章',chapter=2,rewrite_id='synthetic_author_rewrite',book_id=state['book_id'],automation_id=self.owner['automation_id'],expected_state_sha256=rw.hash_file(self.root/'state.json'),old_text_sha256=state['chapters']['2']['text_sha256'],old_record_sha256=state['chapters']['2']['record_sha256'])
        self.gpath='.automation/rewrites/synthetic_grant.json';ns.atomic_json(self.project/self.gpath,self.g)
        self.req=dict(controller=self.owner,rewrite_grant=self.gpath)
    def test_begin_preserves_formal_state_and_repair_budget(self):
        before=(self.root/'state.json').read_bytes();a=rw.begin(self.project,self.req);view=rw.workspace(self.project,a);prefix=ns.load_state(view/'book')
        self.assertEqual((self.root/'state.json').read_bytes(),before);self.assertEqual(len(prefix['chapters']),1);self.assertEqual(prefix['tasks']['2']['repairs'],2)
        self.assertEqual(rw.begin(self.project,self.req)['run_id'],a['run_id'])
        with self.assertRaises(daily.ns.WorkspaceError):daily.begin(self.project,trigger_id='synthetic_new_schedule')
    def test_wrong_authorization_and_wrong_base_are_rejected(self):
        self.req['controller']=dict(self.owner,invocation_kind='scheduled')
        with self.assertRaises(rw.ns.WorkspaceError):rw.begin(self.project,self.req)
        self.req['controller']=self.owner;self.g['expected_state_sha256']='0'*64;ns.atomic_json(self.project/self.gpath,self.g)
        with self.assertRaises(rw.ns.WorkspaceError):rw.begin(self.project,self.req)
    def test_atomic_adoption_preserves_old_files_and_old_receipt(self):
        before=ns.load_state(self.root);oldbody=(self.root/before['chapters']['2']['text_path']).read_bytes();a=rw.begin(self.project,self.req);v=rw.workspace(self.project,a);root=v/'book';body='乙归还钥匙并承担修复门锁的费用。';draft=self.project/'drafts/new.txt';draft.write_text(body)
        ns.candidate(root,2,draft,request='合成明确重写',repairing=True);state=ns.load_state(root);rec=self.record(2,body);rec['base_revision']=state['revision'];recpath=self.project/'drafts/record.json';ns.atomic_json(recpath,rec)
        report=ns.serial_module().skeleton(root,state,body,rec,ns.serial_api());ev=dict(chapter=2,sha256=ns.digest(body),quote=body)
        report.update(decision='release',read_full_text=True,blocking_issues=[],reader_reason_to_continue='合成',consequence='合成',ending_runway='合成')
        for x in report['checks'].values():x.update(decision='pass',conclusion='合成机制证据',evidence=[ev])
        for x in report['comparison']:x.update(quote='甲把钥匙交给乙。',difference='合成差异')
        for x in report['future_window']:x.update(goal='合成',change='合成',ending_question='合成')
        reportpath=self.project/'drafts/report.json';ns.atomic_json(reportpath,report)
        request=dict(self.req,run_id=a['run_id'],text='drafts/new.txt',record='drafts/record.json',report='drafts/report.json')
        result=rw.finalize(self.project,request);after=ns.load_state(self.root);rw.guard(self.project,before,after)
        self.assertEqual(after['chapters']['1'],before['chapters']['1']);self.assertEqual(after['tasks']['2']['repairs'],3);self.assertEqual((self.root/before['chapters']['2']['text_path']).read_bytes(),oldbody)
        self.assertEqual(daily.verify_receipt(self.root,after,self.receipt),self.receipt)
        self.assertEqual(daily.begin(self.project,trigger_id='synthetic_original_trigger')['action'],'already_completed')
        self.assertEqual(daily.begin(self.project,trigger_id='synthetic_next_schedule')['chapter'],3)
        self.assertEqual(result['business_status'],'chapter_completed')
