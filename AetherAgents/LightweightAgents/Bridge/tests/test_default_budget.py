"""Every project has a budget: the brief's own, or a default the host announces once and a note can change.
Synthetic records only; no model is called.
"""
from aether.atelier import Atelier, facts

OPERATOR = dict(id='operator', kind='operator', name='Operator')


def opened(tmp_path, text):
    atelier = Atelier(tmp_path / 'share', 'c-budget')
    atelier.open(OPERATOR, text)
    return atelier


def budget_questions(state):
    """The host's budget notice, whether already posted at opening or still to ask."""
    return [q for q in state['questions_asked'] + facts.questions(state) if q['about'] == 'budget']


def test_a_brief_without_a_budget_gets_the_default_and_is_told_so(tmp_path):
    atelier = opened(tmp_path, 'Write a small report about the harbor.')
    state = atelier.facts()
    assert state['campaign']['budget_model_calls'] == facts.DEFAULT_BUDGET and state['campaign']['budget_default']
    assert atelier.budget_left(state)['model_calls_limit'] == facts.DEFAULT_BUDGET
    [question] = budget_questions(state)
    assert question['to'] == 'operator' and 'Budget: N model calls' in question['text']


def test_a_budget_in_plain_words_is_read(tmp_path):
    for text, calls in (('Write a report. Budget: 12 model calls.', 12), ('Write a report with a budget of 40 calls.', 40),
                        ('Write a report; the budget is about 25 model calls.', 25)):
        state = opened(tmp_path / str(calls), text).facts()
        assert state['campaign']['budget_model_calls'] == calls and not state['campaign'].get('budget_default')
        assert not budget_questions(state)


def test_a_later_note_replaces_the_default(tmp_path):
    atelier = opened(tmp_path, 'Write a small report about the harbor.')
    atelier.post(OPERATOR, 'Budget: 100 model calls.')
    state = atelier.facts()
    assert state['campaign']['budget_model_calls'] == 100 and not state['campaign']['budget_default']
    assert atelier.budget_left(state)['model_calls_limit'] == 100
    assert not [q for q in facts.questions(state) if q['about'] == 'budget']       # not asked again
