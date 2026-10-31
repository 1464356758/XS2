"""明确作者授权的末章版本更换；旧文、回执和累计返修记录保持可核。"""
from __future__ import annotations
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import novel_state as ns
import daily_update as daily
import online_state as online


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def active(project):
    p = Path(project) / 'book/rewrites/active.json'
    return ns.read_json(p) if p.exists() else None


def grant(project, request):
    value = request.get('rewrite_grant')
    ns.require(isinstance(value, str) and value.startswith('.automation/rewrites/') and '..' not in Path(value).parts, '缺少独立保存的作者重写授权。')
    g = ns.read_json(ns.inside(project, value))
    ns.require(g.get('type') == 'novel-author-rewrite-v1' and g.get('authorization_basis') == 'explicit_author_rewrite_request' and
               isinstance(g.get('author_instruction'), str) and '第二章' in g['author_instruction'] and g.get('chapter') == 2,
               '本入口目前仅处理明确授权的第二章重写，不供日更自行改旧章。')
    ns.require(request['controller'].get('invocation_kind') == 'author_manual', '作者重写须是有授权的手动执行。')
    cfg = ns.read_json(Path(project) / '.automation/config.json')
    ns.require(g.get('book_id') == cfg['book_id'] and g.get('automation_id') == cfg['automation_id'], '授权不是本书或本任务。')
    daily.trigger_path(g.get('rewrite_id'))
    return g


def workspace(project, a):
    return Path(project) / '.automation/work' / a['rewrite_id'] / 'project'


def assert_owned(project, request):
    import task_executor as executor
    a = active(project)
    ns.require(a and a['status'] in ('running', 'blocked') and a['run_id'] == request.get('run_id') and
               executor.owner_equal(a['controller'], request['controller']), '重写归属不匹配，不能抢占。')
    g = grant(project, request)
    ns.require(g['rewrite_id'] == a['rewrite_id'], '重写授权改变。')
    ns.require(hash_file(Path(project) / 'book/state.json') == a['source_state_sha256'], '重写期间正式版本已变，停止覆盖。')
    return a


def begin(project, request):
    import task_executor as executor
    project = Path(project); root = project / 'book'; g = grant(project, request); old = active(project)
    if old and old['rewrite_id'] == g['rewrite_id']:
        ns.require(executor.owner_equal(old['controller'], request['controller']), '既有重写控制者不同，先核实原执行。')
        return dict(old, business_status='already_completed' if old['status'] == 'completed' else 'continue_owned')
    ns.require(not old or old['status'] == 'completed', '已有未完成重写，不能另建。')
    state = ns.load_state(root); ns.verify_files(root, state)
    ns.require(len(state['chapters']) == g['chapter'] and not any(t['phase'] == 'candidate' for t in state['tasks'].values()), '仅允许重写当前末章；不能越过后续正文。')
    ns.require(state['book_id'] == g['book_id'] and hash_file(root / 'state.json') == g['expected_state_sha256'] and
               state['chapters']['2']['text_sha256'] == g['old_text_sha256'] and state['chapters']['2']['record_sha256'] == g['old_record_sha256'], '授权基础版本已改变。')
    d = daily.active_record(root)
    ns.require(not d or d['status'] == 'completed', '日更目标仍未完成，不能并发重写。')
    archive = root / 'rewrites' / g['rewrite_id']; archive.mkdir(parents=True, exist_ok=True)
    ns.immutable_text(archive / 'source_state.json', (root / 'state.json').read_text())
    ns.immutable_text(archive / 'authorization.json', json.dumps(g, ensure_ascii=False, indent=2) + '\n')
    if d: ns.immutable_text(archive / 'source_daily_active.json', (root / 'daily/active.json').read_text())
    view = workspace(project, g); view.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root, view / 'book', ignore=shutil.ignore_patterns('rewrites', 'daily', '.writer-lock'), dirs_exist_ok=True)
    shutil.copytree(project / 'tools', view / 'tools', ignore=shutil.ignore_patterns('__pycache__'), dirs_exist_ok=True)
    shutil.copytree(project / '.automation', view / '.automation', ignore=shutil.ignore_patterns('work', 'results'), dirs_exist_ok=True)
    for name in ('project_config.json', 'platforms.json'):
        if (project / name).is_file(): shutil.copyfile(project / name, view / name)
    prefix = copy.deepcopy(state)
    del prefix['chapters']['2']; prefix['serialization'] = ns.serial_module().new_state(prefix['config'], ns.serial_api())
    for n, item in prefix['chapters'].items():
        gate = state['serialization']['gates'][n]; report = ns.read_json(root / gate['path'])
        prefix['serialization']['gates'][n] = copy.deepcopy(gate)
        ns.serial_module().on_adopt(prefix, int(n), item, report, ns.serial_api())
        if n in state['serialization']['published']: prefix['serialization']['published'][n] = copy.deepcopy(state['serialization']['published'][n])
    prefix['checkpoints'] = [x for x in state['checkpoints'] if x['to'] < 2]
    prefix['tasks']['2']['phase'] = 'candidate'
    prefix.setdefault('writing_briefs', {}).pop('2', None)
    ns.atomic_json(view / 'book/state.json', prefix)
    run = daily.begin(view, trigger_id=g['rewrite_id'])
    run['controller'] = copy.deepcopy(request['controller']); ns.atomic_json(view / 'book/daily/active.json', run)
    a = dict(rewrite_id=g['rewrite_id'], book_id=state['book_id'], chapter=2, status='running', run_id=run['run_id'],
             controller=request['controller'], source_state_sha256=g['expected_state_sha256'], grant_path=request['rewrite_grant'],
             started_at=run['started_at'], previous_repairs=state['tasks']['2']['repairs'])
    ns.atomic_json(root / 'rewrites/active.json', a)
    wf = online.load_workflow(project); online.write_workflow(project, wf, phase='WRITING', detail='作者明确授权重写第二章，正式旧版保留至新版检查采用；累计返修不清零。', next_action='在授权前文视图完成任务书、正文、实读与检查。')
    wf = online.load_workflow(project); wf['chapter'] = 2; ns.atomic_json(root / 'workflow.json', wf)
    return dict(a, business_status='started')


def command(project, request, result):
    import task_executor as executor
    a = assert_owned(project, request); view = workspace(project, a)
    # Only staged drafts are inputs. Formal files and source code are never
    # accepted from request payloads into the editorial view.
    shutil.copytree(Path(project) / 'drafts', view / 'drafts', dirs_exist_ok=True)
    commands = request.get('commands')
    ns.require(isinstance(commands, list) and 1 <= len(commands) <= 6, '每批1—6个原有工具。')
    for i, c in enumerate(commands):
        ns.require(c.get('script') == 'novel_state.py' and c['args'][2] not in ('set-input', 'incident'), '重写只操作本次正文与审核，不改设定或其他正式事项。')
        cmd = executor.command_spec(view, c)
        done = subprocess.run(cmd, cwd=view, capture_output=True, text=True, timeout=120)
        outputs = {}
        for channel, body in (('stdout', done.stdout), ('stderr', done.stderr)):
            path = Path(project) / '.automation/work' / request['request_id'] / f'tool-{i+1:02d}.{channel}.txt'
            path.parent.mkdir(parents=True, exist_ok=True); path.write_text(body)
            outputs[channel] = dict(path=path.relative_to(project).as_posix(), sha256=hash_file(path), bytes=path.stat().st_size)
        result.setdefault('tools', []).append(dict(script=c['script'], args=c['args'], exit_code=done.returncode, outputs=outputs))
        ns.require(done.returncode == 0, f'重写工具第{i+1}步失败，完整输出已保留。')
    for p in (view / 'drafts').rglob('*'):
        if p.is_file():
            target = Path(project) / 'drafts' / p.relative_to(view / 'drafts'); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(p.read_bytes())
    result.update(business_status='tools_completed', run_id=a['run_id'], chapter=2,
                  view_state_path=(view / 'book/state.json').relative_to(project).as_posix())


def finalize(project, request):
    project = Path(project); root = project / 'book'; a = assert_owned(project, request); view = workspace(project, a)
    shutil.copytree(project / 'drafts', view / 'drafts', dirs_exist_ok=True)
    paths = [ns.inside(view, request[k]) for k in ('text', 'record', 'report')]
    before = ns.load_state(root)
    ns.serial_module().check(view / 'book', *paths, ns.serial_api())
    ns.adopt(view / 'book', paths[0], paths[1])
    state = ns.load_state(view / 'book')
    ns.require(state['tasks']['2']['repairs'] >= a['previous_repairs'] and state['tasks']['2']['task_id'] == before['tasks']['2']['task_id'], '不可清零修订或换任务。')
    ns.require(state['chapters']['1'] == before['chapters']['1'], '第一章只读。')
    for folder in ('candidates', 'briefs', 'chapters', 'records', 'releases'):
        for p in (view / 'book' / folder).rglob('*'):
            if p.is_file(): ns.immutable_text(root / p.relative_to(view / 'book'), p.read_text())
    state['replaced_chapters'] = before.get('replaced_chapters', []) + [dict(chapter=2, rewrite_id=a['rewrite_id'],
        old_text_sha256=before['chapters']['2']['text_sha256'], old_record_sha256=before['chapters']['2']['record_sha256'],
        source_state_path='rewrites/' + a['rewrite_id'] + '/source_state.json', source_state_sha256=a['source_state_sha256'],
        author_grant_path=a['grant_path'], replacement_text_sha256=state['chapters']['2']['text_sha256'])]
    ns.atomic_json(root / 'state.json', state)
    run = ns.read_json(view / 'book/daily/active.json'); ns.atomic_json(root / 'daily/active.json', run)
    receipt = daily.finish(project, run['run_id'])
    a.update(status='completed', completed_at=receipt['completed_at'], receipt_path='book/' + receipt['receipt_path'], new_text_sha256=receipt['body_sha256'])
    ns.atomic_json(root / 'rewrites/active.json', a)
    ns.immutable_text(root / 'rewrites' / a['rewrite_id'] / 'completion.json', json.dumps(a, ensure_ascii=False, indent=2) + '\n')
    wf = online.load_workflow(project); online.write_workflow(project, wf, phase='WAITING_NEXT', detail='作者授权的第二章新版已按原审核采用冻结；旧版本与回执保留。', next_action='下一次定时按最新正式第二章续写第三章。')
    wf = online.load_workflow(project); wf['chapter'] = 3; ns.atomic_json(root / 'workflow.json', wf)
    return dict(business_status='chapter_completed', operation='author_rewrite', run_id=receipt['run_id'], chapter=2,
                body_sha256=receipt['body_sha256'], receipt_path='book/' + receipt['receipt_path'])


def guard(project, old, state):
    a = active(project)
    ns.require(a and a['status'] == 'completed' and len(old['chapters']) == len(state['chapters']) == 2, '重写只更换末章，不增减进度。')
    ns.require(hash_file(Path(project) / 'book/rewrites' / a['rewrite_id'] / 'source_state.json') == a['source_state_sha256'], '旧状态归档失真。')
    archived = ns.read_json(Path(project) / 'book/rewrites' / a['rewrite_id'] / 'source_state.json')
    ns.require(archived == old and state['revision'] == old['revision'] + 1, '版本更换须绑定本次真实基础状态。')
    for n in old['chapters']:
        if n != '2': ns.require(state['chapters'][n] == old['chapters'][n] and state['serialization']['frozen'][n] == old['serialization']['frozen'][n], '其他冻结章不可变。')
    ns.require(state['tasks']['2']['task_id'] == old['tasks']['2']['task_id'] and state['tasks']['2']['repairs'] >= old['tasks']['2']['repairs'], '累计修订不能清零。')
