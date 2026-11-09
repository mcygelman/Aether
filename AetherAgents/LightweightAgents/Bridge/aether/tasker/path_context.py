"""Render runtime-owned paths from the tool bindings; never grant access or alter identities or task state."""
import json


def _render(paths, defaults):
    return ('⟪RUNTIME_PATHS⟫\n' + json.dumps(paths, ensure_ascii=False, indent=2) +
            '\n⟪TOOL_DEFAULTS⟫\n' + defaults + '\n⟪END_RUNTIME_PATHS⟫')


def staff_context(tools):
    from .verification import summary
    sprint = tools.sprint
    task = tools.tasker.get(tools.wake.task) if tools.wake.task else None
    active = tools.active()
    own = str(sprint.outputs(task.number).resolve()) if task and task.assigned_to == tools.agent else None
    return _render(dict(
        sprint_root=str(tools.root), read_file_base=str(tools.root),
        task_output_dir=own,
        declared_outputs=summary(sprint).get(str(task.number), []) if task else [],
        active_command_cwd=str(sprint.outputs(active).resolve()) if active else None,
        dependency_output_dirs={f'T{n}': str(sprint.outputs(n).resolve()) for n in task.depends_on} if own else {},
        granted_input_roots=[str(p) for p in tools.grants()],
        permitted_command_dirs=[str(p) for p in tools.runnable()],
        briefcase=str(sprint.briefcase(tools.agent).resolve())),
        'On a TASK wake, task_start activates the task_output_dir above.\n'
        'write_file: use the requested bare filename; it writes in your Active task output directory.\n'
        'run_command: omit cwd for your Active task output directory. For another permitted directory, copy its exact path above.\n'
        'read_file/list_dir: relative paths start at read_file_base; use full paths for external inputs and dependency files.\n'
        'Programs resolve relative filenames from their command cwd. A command does not change later tool defaults.\n'
        'These runtime bindings define locations; task prompts name the work and deliverables. Do not reconstruct paths from a run name.')


def overseer_context(sprint):
    from .verification import summary
    record = sprint.record()
    tasks = sprint.tasker('overseer:' + sprint.overseer).list()
    return _render(dict(
        sprint_root=str(sprint.root.resolve()), read_file_base=str(sprint.root.resolve()),
        outputs_root=str((sprint.root/'outputs').resolve()),
        declared_task_outputs=summary(sprint),
        task_output_dirs={f'T{t.number}': str(sprint.outputs(t.number).resolve()) for t in tasks},
        granted_input_roots=record.get('overseer_read_roots', [])),
        'read_file/list_dir: relative paths start at read_file_base; copy full external input paths.\n'
        'Each staff wake supplies its exact task and dependency directories automatically.\n'
        'For staff programs in their own Active task outputs, omit cwd. Do not ask staff to invent or fill a cwd placeholder.\n'
        'For a program in another permitted directory, supply its exact resolved path. Programs resolve relative filenames there.\n'
        'Task prompts specify work, inputs and deliverables; the tools supply path defaults and enforce transitions.')
