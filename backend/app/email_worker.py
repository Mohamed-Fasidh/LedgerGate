"""Standalone process for durable account-security email delivery."""
import logging
from app.services.email_outbox import run_forever

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
