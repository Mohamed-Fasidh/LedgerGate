from rl_research.algorithms.actor_critic import train as train_ac
from rl_research.algorithms.policy_gradient import train as train_pg


def test_reinforce_short_run_saves_checkpoint_and_history(tmp_path):
    config = {"seed": 1, "episodes": 3, "hidden_dim": 16, "learning_rate": 0.005,
              "gamma": 0.99, "eval_interval": 1, "eval_episodes": 3}
    result = train_pg(config, tmp_path, "cpu")
    assert "success_rate" in result
    assert (tmp_path / "policy_gradient" / "checkpoint.pt").exists()
    assert (tmp_path / "policy_gradient" / "metrics.json").exists()


def test_actor_critic_short_run_tracks_actor_and_critic(tmp_path):
    config = {"seed": 1, "episodes": 3, "hidden_dim": 16, "learning_rate": 0.003,
              "gamma": 0.99, "value_coef": 0.5, "entropy_coef": 0.01,
              "eval_interval": 1, "eval_episodes": 3}
    result = train_ac(config, tmp_path, "cpu")
    assert "eval_return" in result
    row = (tmp_path / "actor_critic" / "metrics.csv").read_text()
    assert "critic_loss" in row and "actor_loss" in row
