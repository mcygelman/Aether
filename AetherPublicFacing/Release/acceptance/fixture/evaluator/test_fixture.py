import importlib.util
from pathlib import Path
import subprocess
import sys
import pytest

EVALUATOR_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = EVALUATOR_DIR.parent
ACCEPTANCE_DIR = FIXTURE_DIR.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('project', ['A', 'B'])
def test_frozen_inputs_baseline_seed_and_oracle(tmp_path, project):
    runner = load('acceptance_runner', ACCEPTANCE_DIR / 'runner.py')
    grade = load('fixture_grade', EVALUATOR_DIR / 'grade.py').grade
    oracle = load('fixture_reference', EVALUATOR_DIR / 'reference.py')

    out = Path(tmp_path) / project
    receipt = runner.prepare_workspace(project, out)
    repo = out / 'repository'

    assert receipt['workspace'] and not (out / 'evaluator').exists()
    public = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=repo, capture_output=True, text=True)
    assert public.returncode == 0, public.stderr

    initial = grade(project, repo)
    assert initial['outcome'] == 'FAIL' and initial['checks']['recurring_decimal_defect'].startswith('FAIL')

    (repo / 'harbor/reports.py').write_text(oracle.REPORTS, encoding='utf-8')
    for name in ('docs/OPERATOR_GUIDE.md', 'IMPLEMENTATION.md', 'REVIEW.md'):
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text('Synthetic oracle artifact; not a real independent review.', encoding='utf-8')

    assert grade(project, repo)['outcome'] == 'FAIL'  # Feature alone does not fix the seed.
    (repo / 'harbor/money.py').write_text(oracle.MONEY, encoding='utf-8')

    result = grade(project, repo)
    assert result['outcome'] == 'PASS', result
    assert result['mvp_acceptance'] == 'NOT_RUN' and result['memory_benefit'] == 'NOT_ASSESSED'


def test_b_precision_is_a_real_changed_constraint(tmp_path):
    runner = load('acceptance_runner', ACCEPTANCE_DIR / 'runner.py')
    grade = load('fixture_grade', EVALUATOR_DIR / 'grade.py').grade
    oracle = load('fixture_reference', EVALUATOR_DIR / 'reference.py')

    out = Path(tmp_path) / 'B'
    runner.prepare_workspace('B', out)
    repo = out / 'repository'

    (repo / 'harbor/money.py').write_text(oracle.MONEY, encoding='utf-8')
    (repo / 'harbor/reports.py').write_text(oracle.REPORTS.replace("{'USD':2,'JPY':0,'KWD':3}", "{'USD':2,'JPY':2,'KWD':2}"), encoding='utf-8')

    result = grade('B', repo)
    assert result['checks']['changed_currency_precision'].startswith('FAIL')
    assert result['checks']['jpy_fraction_rejected'].startswith('FAIL')


def test_grader_rejects_float_totals_even_when_numerically_equal(tmp_path):
    runner = load('acceptance_runner', ACCEPTANCE_DIR / 'runner.py')
    grade = load('fixture_grade', EVALUATOR_DIR / 'grade.py').grade
    oracle = load('fixture_reference', EVALUATOR_DIR / 'reference.py')

    out = Path(tmp_path) / 'A'
    runner.prepare_workspace('A', out)
    repo = out / 'repository'

    (repo / 'harbor/money.py').write_text(oracle.MONEY, encoding='utf-8')
    (repo / 'harbor/reports.py').write_text(oracle.REPORTS.replace("group['total_minor']+=amount", "group['total_minor']+=float(amount)"), encoding='utf-8')
    assert grade('A', repo)['checks']['exact_total_half_open_sorted'].startswith('FAIL')
