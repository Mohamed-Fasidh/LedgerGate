"""Standalone process for durable read-only agent job execution."""
import logging
from app.services.agent_jobs import run_forever

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
