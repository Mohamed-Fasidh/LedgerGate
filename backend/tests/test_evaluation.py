from app.services.evaluation import run_evaluation

def test_evaluation_suite_passes_all_cases():
    result=run_evaluation()
    assert result["total"] == 5
    assert result["passed"] == 5
