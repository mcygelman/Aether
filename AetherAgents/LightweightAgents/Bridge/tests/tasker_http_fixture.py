"""Script a loopback model for CLI transport tests; never contact a provider or change product state."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


S1 = ('∆WMC{\nWORLD_MODEL := ⌜SAGAN_WORKSPACE⌝\nCONTEXT: SPRINT_CLOSED\n'
      '[LOADED_COMPONENTS] := { System_0, System_2 anchors }\n[GOALS] := { G1: "Copied the input" }\n'
      '[NEXT_ACTION] := ⊕("Wait for the next sprint.")\n}∆WMC')
S2 = ('⟪MICRO_REFLECTION_INDEX_ENTRY_BEGIN⟫\n[ID] := MICRO_REF_20261031_TRANSPORT\n'
      '[DATE] := 2026-10-31\n[AGENT] := Sagan\n[TOPIC] := CLI transport\n[IS_MICRO] := TRUE\n'
      '[ANCHOR] := ⌜TRANSPORT⌝\n[SUMMARY] := ⌞T1 copied the supplied text.⌟\n'
      '[LESSON] := ⌞Read the exact input path before writing.⌟\n[NEXT_STEP] := ⌞Keep paths explicit.⌟\n'
      '⟪MICRO_REFLECTION_INDEX_ENTRY_END⟫')
CLOSING = '# What I did\nCoordinated one copy task.\n# What I learned\nExplicit paths avoid ambiguity.\n# What my future self should carry\nRead before writing.\n'


class ScriptedServer:
    def __init__(self, source, closure_mode='normal', command_mode=None, staff_closure_mode='normal'):
        self.source = source
        self.closure_mode = closure_mode
        self.command_mode = command_mode
        self.staff_closure_mode = staff_closure_mode
        self.requests, self.errors = [], []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                try:
                    assert self.path == '/v1/chat/completions'
                    assert not self.headers.get('Authorization')
                    request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    message = owner.reply(request)
                    body = json.dumps({'id': 'offline-fixture', 'model': 'fixture-model',
                                       'choices': [{'index': 0, 'message': message, 'finish_reason': 'tool_calls' if message.get('tool_calls') else 'stop'}],
                                       'usage': {'prompt_tokens': 100, 'completion_tokens': 20}}).encode()
                    self.send_response(200)
                except Exception as exc:
                    owner.errors.append(repr(exc))
                    body = json.dumps({'error': str(exc)}).encode()
                    self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()

    @property
    def endpoint(self):
        return f'http://127.0.0.1:{self.server.server_port}/v1'

    def reply(self, request):
        self.requests.append(request)
        messages = request['messages']
        system = messages[0]['content']
        assert system.startswith('⟪RUNTIME_PATHS⟫\n')
        notice = next(m['content'] for m in messages if m['role'] == 'user')
        step = sum(m['role'] == 'assistant' for m in messages)
        for m in messages:
            if m['role'] == 'tool':
                assert not m['content'].startswith(('refused:', 'ValueError:', 'PermissionError:', 'FileNotFoundError:', 'StaffError:')), m['content']
        calls = []
        if '=== OVERSEER DIRECTIVE ===' in system:
            paths = json.loads(system.split('\n',1)[1].split('\n⟪TOOL_DEFAULTS⟫',1)[0])
            assert str(self.source.parent) in paths['granted_input_roots']
            if '⟪KIND::HIRED⟫' in notice and step == 0:
                calls = [('teachframe_list', {}), ('read_file', {'path': str(self.source)}),
                         ('staff_hire', {'agent_ids': ['sagan']}),
                         ('briefcase_create', {'agent_id': 'sagan', 'teachframes': ['ToolUse_Discipline'],
                                               'read_roots': [str(self.source.parent)]}),
                         ('task_add', {'title': 'Copy exact input', 'prompt': '⟪TASK⟫ Read ' + str(self.source) +
                                      '; write only COPY.txt with exactly those bytes as UTF-8. Reopen before task_close.', 'assigned_to': 'sagan'}),
                         ('timer_start', {})]
            elif '⟪KIND::QUIET⟫' in notice and step == 0:
                calls = [('closure_send', {})]
            elif '⟪KIND::CLOSURE_DONE⟫' in notice and step == 0:
                if 'OVERSEER_CLOSURE_MISSING' in notice:
                    calls = [('give_closing_words', {'text': CLOSING})] if self.closure_mode == 'recover' else []
                else:
                    calls = [('sprint_finish', {'result': 'pass', 'note': 'Copied and checked the input.'})]
                    if self.closure_mode == 'normal':
                        calls.append(('give_closing_words', {'text': CLOSING}))
        elif '⟪WAKE::CLOSURE⟫' in system and step == 0:
            if self.staff_closure_mode=='normal' or (self.staff_closure_mode=='recover' and 'STAFF_CLOSURE_MISSING' in notice):
                calls = [('identity_close', {'s1': S1, 's2_entry': S2})]
        elif '⟪WAKE::TASK⟫' in system:
            plan = [[('task_start', {'number': 1}),
                     ('read_file', {'path': 'briefcases/sagan/teachframes/TEACHFRAME_ToolUse_Discipline.txt'}),
                     ('read_file', {'path': str(self.source)})],
                    [('write_file', {'path': 'COPY.txt', 'text': self.source.read_text(encoding='utf-8')})],
                    [('read_file', {'path': 'outputs/T1/COPY.txt'})],
                    [('task_close', {'number': 1, 'note': 'Exact input copied and reopened.'})]]
            operation = lambda code, retry=False: ('run_command', dict(
                argv=['python','-B','-c',f'import os; print(os.getcwd()); raise SystemExit({code})'],
                **({'retry':True} if retry else {})))
            if self.command_mode == 'refuse':
                plan.insert(1, [('run_command', dict(argv=['python','-B','-c','print(1)'],
                             cwd=str(self.source.parent.parent/'outputs/T1')))])
            elif self.command_mode in ('recover','exhaust'):
                plan[1:1] = [[operation(1)], [operation(1,True)],
                             [operation(0 if self.command_mode == 'recover' else 1,True)]]
            else:
                plan.insert(1,[operation(0)])
            calls = plan[step] if step < len(plan) else []
        prose = CLOSING if '=== OVERSEER DIRECTIVE ===' in system and '⟪KIND::CLOSURE_DONE⟫' in notice else 'Done.'
        return {'role': 'assistant', 'content': '' if calls else prose, **({'tool_calls': [
            {'id': f'offline-{len(self.requests)}-{i}', 'type': 'function',
             'function': {'name': name, 'arguments': json.dumps(args)}} for i, (name, args) in enumerate(calls)]} if calls else {})}
