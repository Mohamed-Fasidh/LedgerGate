from app.services.retrieval import hybrid_score

def test_hybrid_retrieval_prefers_matching_text():
    assert hybrid_score("invoice approval duplicate", "Invoice approval policy for duplicate payments") > hybrid_score("invoice approval duplicate", "robot arm motion planning")
