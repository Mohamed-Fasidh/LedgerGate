from __future__ import annotations

import numpy as np


class LineWorld:
    """A deterministic 1-D navigation task with a discrete left/right action.

    Observation: one-hot position, shape [n_positions].
    Action 0 moves left; action 1 moves right. Reaching the right-most cell
    pays +1; each non-terminal step earns `step_cost + progress_reward * movement`.
    The task is intentionally tiny.
    """

    def __init__(
        self,
        n_positions: int = 7,
        max_steps: int = 12,
        step_cost: float = -0.02,
        progress_reward: float = 0.03,
    ):
        if n_positions < 3 or max_steps < 1:
            raise ValueError("n_positions must be >= 3 and max_steps >= 1")
        self.n_positions = n_positions
        self.max_steps = max_steps
        self.step_cost = step_cost
        self.progress_reward = progress_reward
        self.position = 0
        self.steps = 0

    @property
    def observation_size(self) -> int:
        return self.n_positions

    @property
    def action_size(self) -> int:
        return 2

    def _observation(self) -> np.ndarray:
        observation = np.zeros(self.n_positions, dtype=np.float32)
        observation[self.position] = 1.0
        return observation

    def reset(self, seed: int | None = None) -> tuple[np.ndarray, dict]:
        del seed  # Environment dynamics are deterministic.
        self.position = 0
        self.steps = 0
        return self._observation(), {"position": self.position}

    def step(self, action: int) -> tuple[np.ndarray, float, bool, bool, dict]:
        if action not in (0, 1):
            raise ValueError("action must be 0 (left) or 1 (right)")
        old_position = self.position
        self.position = int(
            np.clip(self.position + (1 if action == 1 else -1), 0, self.n_positions - 1)
        )
        movement = self.position - old_position
        self.steps += 1
        terminated = self.position == self.n_positions - 1
        truncated = self.steps >= self.max_steps and not terminated
        reward = 1.0 if terminated else self.step_cost + self.progress_reward * movement
        return self._observation(), reward, terminated, truncated, {
            "position": self.position,
            "success": terminated,
        }
