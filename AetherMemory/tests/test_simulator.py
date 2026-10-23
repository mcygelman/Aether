import copy
import os

from memory_fabric import simulator

SCENARIOS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scenarios")


def test_whole_scenario_library_passes():
    reports = simulator.run_library(SCENARIOS)
    assert len(reports) >= 8
    failing = [r.render() for r in reports if not r.passed]
    assert not failing, "\n".join(failing)


def test_bench_detects_a_wrong_expectation():
    scenario = copy.deepcopy(simulator.load_scenario(os.path.join(SCENARIOS, "01_upstairs_door.json")))
    scenario["steps"][3]["expect_surface"] = ["door_code"]     # deliberately wrong
    report = simulator.run_scenario(scenario)
    assert not report.passed
    assert "door_code" in report.render()


def test_calibration_overrides_change_outcomes():
    scenario = simulator.load_scenario(os.path.join(SCENARIOS, "01_upstairs_door.json"))
    # if project facts barely decayed after the switch, the stale path would still surface
    report = simulator.run_scenario(scenario, profiles={"project_fact": {"fast_rate": 0.001}})
    assert not report.passed
